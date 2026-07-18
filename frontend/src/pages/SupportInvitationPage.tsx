import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { supportApi } from "../api/support";
import { useAuth } from "../contexts/AuthContext";
import { parseApiError } from "../lib/apiErrors";

function invitationReturnPath(token: string) {
  return `/support/invitations/accept?token=${encodeURIComponent(token)}`;
}

export function SupportInvitationPage() {
  const { user, isAuthenticated, isLoading } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const token = searchParams.get("token")?.trim() || "";
  const [error, setError] = useState<string | null>(null);

  const preview = useQuery({
    queryKey: ["support-invitation-preview", token],
    queryFn: ({ signal }) => supportApi.previewInvitation(token, signal),
    enabled: Boolean(token),
    retry: false,
  });
  const accept = useMutation({
    mutationFn: () => supportApi.acceptInvitation(token),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
      navigate("/support", { replace: true });
    },
    onError: (reason) => setError(parseApiError(reason, "The invitation could not be accepted.").message),
  });

  if (!token) return <Navigate to="/support" replace />;
  const returnPath = invitationReturnPath(token);

  return (
    <main id="main-content" className="ms-support-invitation-page" tabIndex={-1}>
      <section className="ms-support-invitation-card" aria-busy={preview.isLoading || accept.isPending}>
        <div className="ms-support-invitation-mark" aria-hidden="true">S</div>
        {preview.isLoading || isLoading ? <div className="ms-support-loading" role="status"><span aria-hidden="true" />Checking invitation…</div> : null}
        {preview.isError ? (
          <div className="ms-support-invitation-copy" role="alert">
            <p className="ms-support-access-state__eyebrow">Invitation unavailable</p>
            <h1>This Support Chat invitation cannot be used</h1>
            <p>{parseApiError(preview.error, "The link is invalid, expired, or has been revoked.").message}</p>
            <Link className="ms-button ms-button--primary" to={isAuthenticated ? "/support" : "/login"}>Continue</Link>
          </div>
        ) : null}
        {preview.data ? (
          <div className="ms-support-invitation-copy">
            <p className="ms-support-access-state__eyebrow">Support Chat agent invitation</p>
            <h1>Join the support team</h1>
            <p>{preview.data.inviter?.display_name || "The Support Chat owner"} invited <strong>{preview.data.invited_email}</strong> to handle website visitor conversations.</p>
            <div className="ms-support-invitation-websites">
              <strong>Website access</strong>
              {preview.data.websites.map((website) => <span key={website.id}>{website.name}<small>{website.domain}</small></span>)}
            </div>
            {!preview.data.account_access_active ? <div className="ms-page-error" role="alert">The owner must renew Support Chat before this invitation can be accepted.</div> : null}
            {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
            {!isAuthenticated ? (
              <div className="ms-support-invitation-actions">
                <Link className="ms-button ms-button--primary" to={`/login?next=${encodeURIComponent(returnPath)}`}>Sign in to accept</Link>
                <Link className="ms-button ms-button--ghost" to={`/register?next=${encodeURIComponent(returnPath)}`}>Create account</Link>
              </div>
            ) : (
              <div className="ms-support-invitation-actions">
                <div className="ms-support-signed-in-as">Signed in as <strong>{user?.email || user?.username}</strong></div>
                <button className="ms-button ms-button--primary" type="button" disabled={!preview.data.valid || accept.isPending} onClick={() => accept.mutate()}>{accept.isPending ? "Joining…" : "Accept invitation"}</button>
              </div>
            )}
            <p className="ms-support-invitation-note">Joining Support Chat does not create a Messenger friendship or expose personal conversations, calls, contacts, or encryption devices.</p>
          </div>
        ) : null}
      </section>
    </main>
  );
}
