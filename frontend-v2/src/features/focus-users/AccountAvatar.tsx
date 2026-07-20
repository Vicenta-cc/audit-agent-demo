import { UserRound } from "lucide-react";

interface AccountAvatarProps {
  name: string;
  src?: string;
  variant: "list" | "detail";
}

export function AccountAvatar({ name, src, variant }: AccountAvatarProps) {
  const className = variant === "detail" ? "account-detail-avatar" : "account-list-avatar";

  if (src) {
    return <img className={className} src={src} alt={`${name}头像`} />;
  }

  return (
    <span className={`${className} account-avatar-fallback`} role="img" aria-label={`${name}默认头像`}>
      <UserRound aria-hidden="true" />
    </span>
  );
}
