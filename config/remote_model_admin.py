from decimal import Decimal

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.forms.models import model_to_dict


MODEL_ACTIONS = [
    {"code": "django.models.catalog", "label": "List Django models", "description": "Return all centrally manageable Django models.", "method": "POST", "dangerous": False},
    {"code": "django.model.list", "label": "List model records", "description": "List records for a model. Payload: {\"model\":\"app.Model\"}.", "method": "POST", "dangerous": False},
    {"code": "django.model.get", "label": "Get model record", "description": "Get one record. Payload: {\"model\":\"app.Model\",\"pk\":1}.", "method": "POST", "dangerous": False},
    {"code": "django.model.create", "label": "Create model record", "description": "Create one record. Payload: {\"model\":\"app.Model\",\"fields\":{...}}.", "method": "POST", "dangerous": True},
    {"code": "django.model.update", "label": "Update model record", "description": "Update one record. Payload: {\"model\":\"app.Model\",\"pk\":1,\"fields\":{...}}.", "method": "POST", "dangerous": True},
    {"code": "django.model.delete", "label": "Delete model record", "description": "Delete one record. Payload: {\"model\":\"app.Model\",\"pk\":1,\"confirm_dangerous\":true}.", "method": "POST", "dangerous": True},
]


def model_catalog():
    items = []
    for model in apps.get_models():
        opts = model._meta
        fields = []
        for field in opts.fields:
            fields.append(
                {
                    "name": field.name,
                    "type": field.get_internal_type(),
                    "editable": bool(getattr(field, "editable", False)) and not field.primary_key,
                    "required": not getattr(field, "blank", True) and getattr(field, "null", False) is False,
                    "primary_key": field.primary_key,
                    "relation": getattr(getattr(field, "remote_field", None), "model", None)._meta.label if getattr(field, "remote_field", None) else "",
                }
            )
        items.append(
            {
                "label": opts.label,
                "app": opts.app_label,
                "model": opts.object_name,
                "verbose_name": str(opts.verbose_name),
                "verbose_name_plural": str(opts.verbose_name_plural),
                "fields": fields,
                "count": safe_count(model),
            }
        )
    return {"models": sorted(items, key=lambda item: item["label"])}


def execute_model_action(action, payload):
    if action == "django.models.catalog":
        return 200, model_catalog()
    if action == "django.model.list":
        return 200, list_records(payload)
    if action == "django.model.get":
        return 200, {"record": serialize_instance(get_instance(payload))}
    if action == "django.model.create":
        instance = build_instance(get_model(payload), payload.get("fields", {}))
        instance.full_clean()
        instance.save()
        return 200, {"status": "ok", "record": serialize_instance(instance)}
    if action == "django.model.update":
        instance = get_instance(payload)
        assign_fields(instance, payload.get("fields", {}))
        instance.full_clean()
        instance.save()
        return 200, {"status": "ok", "record": serialize_instance(instance)}
    if action == "django.model.delete":
        if payload.get("confirm_dangerous") is not True:
            return 400, {"detail": "delete requires confirm_dangerous=true"}
        instance = get_instance(payload)
        serialized = serialize_instance(instance)
        instance.delete()
        return 200, {"status": "ok", "deleted": serialized}
    return None


def get_model(payload):
    label = payload.get("model", "")
    if "." not in label:
        raise ValidationError("model must be in app_label.ModelName format")
    app_label, model_name = label.split(".", 1)
    model = apps.get_model(app_label, model_name)
    if model is None:
        raise ValidationError(f"unknown model: {label}")
    return model


def get_instance(payload):
    model = get_model(payload)
    pk = payload.get("pk")
    if pk in {None, ""}:
        raise ValidationError("pk is required")
    return model._default_manager.get(pk=pk)


def list_records(payload):
    model = get_model(payload)
    limit = min(max(int(payload.get("limit", 25)), 1), 100)
    search = str(payload.get("search", "")).strip()
    queryset = model._default_manager.all()
    if search:
        query = models.Q()
        for field in model._meta.fields:
            if isinstance(field, (models.CharField, models.TextField, models.EmailField, models.SlugField)):
                query |= models.Q(**{f"{field.name}__icontains": search})
        if query:
            queryset = queryset.filter(query)
    order_field = "-pk"
    try:
        model._meta.get_field("created_at")
        order_field = "-created_at"
    except FieldDoesNotExist:
        pass
    records = [serialize_instance(item) for item in queryset.order_by(order_field)[:limit]]
    return {"model": model._meta.label, "records": records, "limit": limit}


def build_instance(model, fields):
    instance = model()
    assign_fields(instance, fields)
    return instance


def assign_fields(instance, fields):
    if not isinstance(fields, dict):
        raise ValidationError("fields must be an object")
    for name, value in fields.items():
        field = instance._meta.get_field(name)
        if field.primary_key or not getattr(field, "editable", False):
            continue
        if isinstance(field, models.ForeignKey):
            setattr(instance, field.attname, value)
        else:
            setattr(instance, name, value)


def serialize_instance(instance):
    data = model_to_dict(instance)
    data["pk"] = instance.pk
    data["__str__"] = str(instance)
    for field in instance._meta.fields:
        value = getattr(instance, field.name)
        if isinstance(field, models.ForeignKey):
            data[field.name] = getattr(instance, field.attname)
        elif hasattr(value, "isoformat"):
            data[field.name] = value.isoformat()
        elif isinstance(value, Decimal):
            data[field.name] = str(value)
    return data


def safe_count(model):
    try:
        return model._default_manager.count()
    except Exception:
        return None
