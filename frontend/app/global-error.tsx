"use client";

import posthog from "posthog-js";
import { useEffect } from "react";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    posthog.captureException(error);
  }, [error]);

  return (
    <html lang="ko">
      <body>
        <main className="boot-screen" role="alert">
          <h1>문제가 발생했습니다</h1>
          <p>페이지를 새로고침한 뒤 다시 시도해 주세요.</p>
        </main>
      </body>
    </html>
  );
}
