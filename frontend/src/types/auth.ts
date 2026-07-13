export type LoginPayload = { username: string; password: string };
export type RegisterPayload = { username: string; email: string; password: string; password_confirm: string };
export type TokenPair = { access: string; refresh: string; session_id?: string; device_id?: string };

export type SocialAccount = {
  provider: string;
  email?: string;
  last_login_at?: string | null;
};

export type UserProfile = {
  display_name?: string;
  bio?: string;
  status_message?: string;
  avatar?: string | null;
  is_discoverable?: boolean;
  show_online_status?: boolean;
  nearby_discovery_enabled?: boolean;
  latitude?: number | null;
  longitude?: number | null;
  location_updated_at?: string | null;
};

export type CurrentUser = {
  id: string;
  username: string;
  email?: string;
  email_verified?: boolean;
  email_verified_at?: string | null;
  first_name?: string;
  last_name?: string;
  display_name?: string;
  full_name?: string;
  is_staff?: boolean;
  is_superuser?: boolean;
  last_seen_at?: string | null;
  profile?: UserProfile | null;
  social_accounts?: SocialAccount[];
};

export type UserSearchResult = {
  id: string;
  username: string;
  first_name?: string;
  last_name?: string;
  full_name?: string;
  display_name?: string;
  avatar?: string | null;
  bio?: string;
  status_message?: string;
  is_current_user?: boolean;
  distance_km?: number | null;
  is_friend?: boolean;
  request_status?: string | null;
  is_online?: boolean;
  last_seen_at?: string | null;
  presence_label?: string;
  presence_visibility?: "public" | "hidden";
};

export type FriendRequest = {
  id: string;
  status: "pending" | "accepted" | "rejected" | "cancelled";
  message?: string;
  created_at?: string;
  responded_at?: string | null;
  from_user: UserSearchResult;
  to_user: UserSearchResult;
};

export type SessionInfo = {
  id: string;
  device_id?: string | null;
  user_agent?: string | null;
  ip_address?: string | null;
  last_seen_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  is_current?: boolean;
};
