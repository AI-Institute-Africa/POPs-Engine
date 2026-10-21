// Production sign-in through Cognify identity (OIDC authorization code + PKCE).
// Enabled when VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID are set at build time.
import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { api, setOidcToken, setRole } from "./api";

const authority = import.meta.env.VITE_OIDC_AUTHORITY as string | undefined;
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID as string | undefined;

export const oidcEnabled = Boolean(authority && clientId);

export const userManager = oidcEnabled
  ? new UserManager({
      authority: authority!,
      client_id: clientId!,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: `${window.location.origin}/login`,
      response_type: "code",
      scope: (import.meta.env.VITE_OIDC_SCOPE as string | undefined) ?? "openid profile email",
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: true,
    })
  : null;

async function adopt(accessToken: string) {
  setOidcToken(accessToken);
  const me = await api<{ role: string }>("/me");
  setRole(me.role);
}

export async function restoreOidcSession(): Promise<boolean> {
  if (!userManager) return false;
  const user = await userManager.getUser();
  if (!user || user.expired) return false;
  await adopt(user.access_token);
  return true;
}

export async function completeOidcLogin(): Promise<void> {
  const user = await userManager!.signinRedirectCallback();
  await adopt(user.access_token);
}

userManager?.events.addUserLoaded((u) => setOidcToken(u.access_token));
