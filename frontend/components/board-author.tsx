import type { User } from "@/lib/types";

export function BoardAuthor({ author }: { author?: Pick<User, "name" | "role"> }) {
  return <span className="board-author"><span className="board-author-name">{author?.name ?? "—"}</span>{author?.role === "admin" ? <span className="board-admin-badge">관리자</span> : null}</span>;
}
