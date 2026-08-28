"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.getAttribute("data-theme") === "dark");
  }, []);

  function toggle() {
    const next = isDark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    setIsDark(!isDark);
  }

  return (
    <button
      className="icon-button"
      onClick={toggle}
      aria-label="다크/라이트 모드 전환"
      title="다크/라이트 모드"
    >
      {isDark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
