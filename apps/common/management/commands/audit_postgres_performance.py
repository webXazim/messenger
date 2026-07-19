from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.chat.models import Conversation, ConversationParticipant, Message, UserBlock
from apps.common.models import RealtimeOutboxEvent
from apps.support.models import (
    SupportConversation,
    SupportConversationReadState,
    SupportWebhookDelivery,
)


CRITICAL_INDEXES = {
    "chat_msg_conv_live_time_idx": Message._meta.db_table,
    "chat_block_reverse_idx": UserBlock._meta.db_table,
    "chat_conver_user_id_6818d1_idx": ConversationParticipant._meta.db_table,
    "chat_conver_type_e9751f_idx": Conversation._meta.db_table,
    "rt_outbox_pending_idx": RealtimeOutboxEvent._meta.db_table,
    "sup_conv_site_stat_upd_idx": SupportConversation._meta.db_table,
    "sup_conv_agent_status_idx": SupportConversation._meta.db_table,
    "sup_conv_read_user_upd_idx": SupportConversationReadState._meta.db_table,
    "sup_hook_del_stat_next_idx": SupportWebhookDelivery._meta.db_table,
}

HOT_TABLES = {
    model._meta.db_table
    for model in (
        Conversation,
        ConversationParticipant,
        Message,
        UserBlock,
        RealtimeOutboxEvent,
        SupportConversation,
        SupportConversationReadState,
        SupportWebhookDelivery,
    )
}

POSTGRES_SETTINGS = (
    "autovacuum",
    "effective_cache_size",
    "idle_in_transaction_session_timeout",
    "maintenance_work_mem",
    "max_connections",
    "max_wal_size",
    "random_page_cost",
    "shared_buffers",
    "work_mem",
)


def _write_json(path: str, payload: dict) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output


class Command(BaseCommand):
    help = "Audit PostgreSQL index validity, hot-table health, and connection pressure without exposing SQL data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
        parser.add_argument("--output", help="Write the JSON report to this path.")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Return a non-zero status for warnings as well as hard failures.",
        )
        parser.add_argument(
            "--long-transaction-seconds",
            type=int,
            default=60,
            help="Treat active/idle-in-transaction sessions older than this as a failure.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("This audit must run against PostgreSQL.")

        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(POSTGRES_SETTINGS))
            cursor.execute(
                f"SELECT name, setting, unit FROM pg_settings WHERE name IN ({placeholders}) ORDER BY name",
                POSTGRES_SETTINGS,
            )
            settings_rows = [
                {"name": name, "setting": setting, "unit": unit or ""}
                for name, setting, unit in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT
                    index_class.relname AS index_name,
                    table_class.relname AS table_name,
                    index_state.indisvalid,
                    index_state.indisready,
                    pg_relation_size(index_class.oid) AS size_bytes,
                    COALESCE(index_stats.idx_scan, 0) AS idx_scan
                FROM pg_index AS index_state
                JOIN pg_class AS index_class ON index_class.oid = index_state.indexrelid
                JOIN pg_class AS table_class ON table_class.oid = index_state.indrelid
                JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                LEFT JOIN pg_stat_user_indexes AS index_stats
                    ON index_stats.indexrelid = index_class.oid
                WHERE namespace.nspname = current_schema()
                ORDER BY table_class.relname, index_class.relname
                """
            )
            indexes = [
                {
                    "name": name,
                    "table": table,
                    "valid": bool(valid),
                    "ready": bool(ready),
                    "size_bytes": int(size_bytes or 0),
                    "scans": int(scans or 0),
                }
                for name, table, valid, ready, size_bytes, scans in cursor.fetchall()
            ]

            table_placeholders = ",".join(["%s"] * len(HOT_TABLES))
            cursor.execute(
                f"""
                SELECT
                    relname,
                    n_live_tup,
                    n_dead_tup,
                    seq_scan,
                    idx_scan,
                    last_autovacuum,
                    last_autoanalyze,
                    vacuum_count,
                    autovacuum_count,
                    analyze_count,
                    autoanalyze_count
                FROM pg_stat_user_tables
                WHERE relname IN ({table_placeholders})
                ORDER BY relname
                """,
                tuple(sorted(HOT_TABLES)),
            )
            tables = []
            for row in cursor.fetchall():
                (
                    name,
                    live_rows,
                    dead_rows,
                    seq_scan,
                    idx_scan,
                    last_autovacuum,
                    last_autoanalyze,
                    vacuum_count,
                    autovacuum_count,
                    analyze_count,
                    autoanalyze_count,
                ) = row
                live_rows = int(live_rows or 0)
                dead_rows = int(dead_rows or 0)
                total_rows = live_rows + dead_rows
                tables.append(
                    {
                        "name": name,
                        "live_rows_estimate": live_rows,
                        "dead_rows_estimate": dead_rows,
                        "dead_row_ratio": round(dead_rows / total_rows, 4) if total_rows else 0.0,
                        "sequential_scans": int(seq_scan or 0),
                        "index_scans": int(idx_scan or 0),
                        "last_autovacuum": last_autovacuum,
                        "last_autoanalyze": last_autoanalyze,
                        "manual_vacuum_count": int(vacuum_count or 0),
                        "autovacuum_count": int(autovacuum_count or 0),
                        "manual_analyze_count": int(analyze_count or 0),
                        "autoanalyze_count": int(autoanalyze_count or 0),
                    }
                )

            timeout_seconds = max(10, int(options["long_transaction_seconds"]))
            cursor.execute(
                """
                SELECT
                    pid,
                    state,
                    EXTRACT(EPOCH FROM (clock_timestamp() - xact_start))::bigint AS age_seconds,
                    wait_event_type,
                    wait_event
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND backend_type = 'client backend'
                  AND pid <> pg_backend_pid()
                  AND xact_start IS NOT NULL
                  AND clock_timestamp() - xact_start > (%s * interval '1 second')
                ORDER BY xact_start
                """,
                [timeout_seconds],
            )
            long_transactions = [
                {
                    "pid": int(pid),
                    "state": state,
                    "age_seconds": int(age_seconds or 0),
                    "wait_event_type": wait_event_type or "",
                    "wait_event": wait_event or "",
                }
                for pid, state, age_seconds, wait_event_type, wait_event in cursor.fetchall()
            ]

            cursor.execute(
                "SELECT count(*), current_setting('max_connections')::int FROM pg_stat_activity"
            )
            current_connections, max_connections = cursor.fetchone()

        indexes_by_name = {item["name"]: item for item in indexes}
        errors: list[str] = []
        warnings: list[str] = []

        critical_results = []
        for index_name, expected_table in CRITICAL_INDEXES.items():
            index = indexes_by_name.get(index_name)
            result = {
                "name": index_name,
                "expected_table": expected_table,
                "present": index is not None,
                "valid": bool(index and index["valid"]),
                "ready": bool(index and index["ready"]),
                "scans": int(index["scans"]) if index else 0,
                "size_bytes": int(index["size_bytes"]) if index else 0,
            }
            critical_results.append(result)
            if index is None:
                errors.append(f"Missing critical index: {index_name}")
            elif index["table"] != expected_table:
                errors.append(
                    f"Critical index {index_name} belongs to {index['table']}, expected {expected_table}."
                )
            elif not index["valid"] or not index["ready"]:
                errors.append(f"Critical index is not valid and ready: {index_name}")

        invalid_indexes = [item for item in indexes if not item["valid"] or not item["ready"]]
        for item in invalid_indexes:
            errors.append(f"Invalid or unfinished PostgreSQL index: {item['name']}")

        for table in tables:
            if table["live_rows_estimate"] >= 5_000 and table["dead_row_ratio"] >= 0.20:
                warnings.append(
                    f"{table['name']} has an estimated dead-row ratio of {table['dead_row_ratio']:.1%}."
                )
            if table["live_rows_estimate"] >= 10_000 and table["last_autoanalyze"] is None:
                warnings.append(f"{table['name']} has no recorded auto-analyze timestamp.")
            if (
                table["live_rows_estimate"] >= 10_000
                and table["sequential_scans"] > max(100, table["index_scans"] * 2)
            ):
                warnings.append(
                    f"{table['name']} has substantially more sequential scans than index scans; inspect plans before adding indexes."
                )

        if long_transactions:
            errors.append(
                f"Found {len(long_transactions)} transaction(s) older than {timeout_seconds} seconds."
            )

        connection_ratio = int(current_connections or 0) / max(1, int(max_connections or 0))
        if connection_ratio >= 0.85:
            errors.append(f"PostgreSQL connection usage is {connection_ratio:.1%}.")
        elif connection_ratio >= 0.70:
            warnings.append(f"PostgreSQL connection usage is elevated at {connection_ratio:.1%}.")

        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database_vendor": connection.vendor,
            "database_version": connection.pg_version,
            "passed": not errors,
            "strict_passed": not errors and not warnings,
            "errors": errors,
            "warnings": warnings,
            "connection_usage": {
                "current": int(current_connections or 0),
                "max": int(max_connections or 0),
                "ratio": round(connection_ratio, 4),
            },
            "settings": settings_rows,
            "critical_indexes": critical_results,
            "invalid_indexes": invalid_indexes,
            "hot_tables": tables,
            "long_transactions": long_transactions,
        }

        if options.get("output"):
            output = _write_json(options["output"], payload)
            self.stderr.write(f"PostgreSQL performance audit written to {output}")

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            style = self.style.SUCCESS if payload["passed"] else self.style.ERROR
            self.stdout.write(style("PASS" if payload["passed"] else "FAIL"))
            self.stdout.write(
                f"Connections: {payload['connection_usage']['current']}/{payload['connection_usage']['max']} "
                f"({payload['connection_usage']['ratio']:.1%})"
            )
            for message in errors:
                self.stdout.write(self.style.ERROR(f"ERROR: {message}"))
            for message in warnings:
                self.stdout.write(self.style.WARNING(f"WARNING: {message}"))

        if errors or (options["strict"] and warnings):
            raise CommandError("PostgreSQL performance audit did not pass.")
