import { ImageResponse } from "next/og";

// Link-preview card (KakaoTalk / Slack / iMessage). Generated here instead of
// shipping a static PNG so the brand mark + wordmark stay in sync with no
// image tooling in the repo. The Korean tagline lives in og:description; the
// image is Latin-only so it needs no bundled CJK font (and no bold weight —
// next/og's built-in font is regular only).
export const runtime = "nodejs";
export const alt = "HAFS PrintQueue";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const BLUE = "#2563EB";

// The three white bars of brand-mark.svg (widths 50/78/106 on a 180 grid),
// scaled to the 220px mark below.
function bar(width: number) {
  return { width, height: 26, borderRadius: 9, background: "#ffffff" };
}

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#f4f6fb",
          borderBottom: `10px solid ${BLUE}`,
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            width: 220,
            height: 220,
            borderRadius: 46,
            background: BLUE,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 13,
          }}
        >
          <div style={bar(61)} />
          <div style={bar(95)} />
          <div style={bar(130)} />
        </div>
        <div style={{ marginTop: 46, fontSize: 78, color: "#0f1420", letterSpacing: -1 }}>
          HAFS PrintQueue
        </div>
        <div style={{ marginTop: 16, fontSize: 30, color: "#5b6472" }}>
          3D Print Queue · printer.hafs.hs.kr
        </div>
      </div>
    ),
    { ...size },
  );
}
