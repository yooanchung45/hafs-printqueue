"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Box, Download, ExternalLink, FileArchive, Upload } from "lucide-react";

type Step = { title: string; image?: string; text: ReactNode; items: string[]; tip?: ReactNode };

const stlSteps: Step[] = [
  {
    title: "STL 파일 올리기",
    image: "/guide/stl-step1.png",
    text: <>출력 신청 페이지에서 <strong>.stl</strong> 파일을 선택하거나 끌어다 놓고 <strong>3D 미리보기</strong>를 누릅니다. 설치할 프로그램은 없습니다.</>,
    items: [
      "여러 파일을 한 번에 올리면 한 판에 모아 한 번의 출력으로 처리합니다.",
      "모델은 MakerWorld·Thingiverse에서 받거나 직접 모델링할 수 있습니다.",
    ],
  },
  {
    title: "크기와 방향 확인하기",
    image: "/guide/stl-step2.png",
    text: <>베드 위에서 <strong>크기</strong>와 <strong>회전</strong>을 조정해 A1 베드(<strong>256 × 256mm</strong>) 안에 들어오게 합니다. 회전 다이얼은 클릭해 각도를 입력하거나 돌려서 맞출 수 있습니다.</>,
    items: [
      "처음이라면 5~8cm 크기를 권장합니다.",
      "베드를 벗어나거나 겹쳐서 빨갛게 표시되면 크기를 줄이거나 위치를 옮기세요.",
      "여러 개는 드래그해 서로 닿지 않게 두거나 자동 배치를 누르세요.",
    ],
  },
  {
    title: "프린터·색상 고르고 신청하기",
    image: "/guide/stl-step3.png",
    text: <>프린터(또는 자동 배정)와 <strong>필라멘트 색상</strong>을 고르고 <strong>출력 신청</strong>을 누릅니다. 서버가 자동으로 슬라이싱하고, 관리자 승인 후 차례대로 출력됩니다.</>,
    items: [
      "받침(서포트)은 자동으로 추가되지 않습니다.",
      "공중에 뜬 부분이 많은 모델은 Bambu Studio로 준비하는 편이 좋습니다.",
    ],
  },
];

const bambuSteps: Step[] = [
  { title: "Bambu Studio 설치하기", image: "/guide/step1.png", text: <>학교 프린터는 <strong>Bambu Lab A1</strong>입니다. <a href="https://bambulab.com/en/download/studio" target="_blank" rel="noopener noreferrer">공식 페이지 <ExternalLink size={13} /></a>에서 Windows 또는 macOS 버전을 설치하세요.</>, items: ["계정 없이도 슬라이싱과 파일 내보내기가 가능합니다.", "처음 실행할 때 로그인 화면은 건너뛰어도 됩니다."] },
  { title: "프린터 선택하기", image: "/guide/step2.png", text: <>프린터 목록에서 <strong>A1</strong>을 선택하세요. A1 mini가 아니며, 노즐은 기본값 <strong>0.4mm</strong>를 사용합니다.</>, items: ["프린터가 다르면 출력 범위와 설정이 맞지 않을 수 있습니다.", "왼쪽 위 프린터 메뉴에서 언제든 A1으로 변경할 수 있습니다."] },
  { title: "모델 불러오기", image: "/guide/step3.png", text: <>출력할 <strong>.stl</strong>, .3mf, .obj 또는 .step 파일을 가져오거나 화면으로 끌어다 놓습니다.</>, items: ["우클릭 드래그로 회전하고 스크롤로 확대합니다.", "모델이 회색 베드 위에 놓였는지 확인하세요."], tip: <>모델은 <a href="https://makerworld.com" target="_blank" rel="noopener noreferrer">MakerWorld</a>나 <a href="https://www.thingiverse.com" target="_blank" rel="noopener noreferrer">Thingiverse</a>에서 받거나 직접 모델링할 수 있습니다.</> },
  { title: "크기와 위치 확인하기", image: "/guide/step4.png", text: <>A1 베드는 <strong>256 × 256mm</strong>입니다. 모델을 선택한 후 Scale과 Move 도구로 베드 안에 배치하세요.</>, items: ["처음이라면 5~8cm 크기의 모델을 권장합니다.", "베드를 벗어나 빨갛게 표시되면 크기를 줄이세요."] },
  { title: "필라멘트 선택하기", image: "/guide/step5.png", text: <>대시보드에서 현재 AMS에 들어 있는 색상과 재질을 확인한 뒤 같은 설정을 선택합니다. 재질은 보통 <strong>PLA</strong>입니다.</>, items: ["처음에는 단색 한 가지로 설정하는 것이 안전합니다.", "화면의 색과 실제 AMS 색이 다르면 실제 출력 색도 달라집니다."] },
  { title: "슬라이싱 하기", image: "/guide/step6.png", text: <>오른쪽 위 <strong>Slice plate</strong>를 누르면 예상 시간과 필라멘트 사용량이 계산됩니다.</>, items: ["처음에는 레이어 0.2mm, 인필 15% 기본값을 권장합니다.", "받침이 필요한 모델은 Support 설정을 확인하세요."] },
  { title: ".gcode.3mf로 내보내기", image: "/guide/step7.png", text: <>File 메뉴에서 <strong>Export plate sliced file</strong>을 선택해 저장합니다.</>, items: ["슬라이싱을 먼저 마친 뒤 내보내야 합니다.", "찾기 쉬운 이름으로 저장하고 출력 신청 페이지에 업로드하세요."] },
];

const faqGroups: { group: string; items: [string, string][] }[] = [
  {
    group: "공통",
    items: [
      ["슬라이싱을 꼭 해야 하나요?", "받침(서포트)이 필요 없는 모델은 안 해도 됩니다. STL 파일을 그대로 올리면 사이트가 자동으로 슬라이싱합니다. Bambu Studio는 받침이나 세밀한 설정이 필요할 때만 쓰는 선택 사항입니다."],
      ["STL 바로 업로드와 Bambu Studio 중 뭘 써야 하나요?", "받침이 필요 없으면 STL 바로 업로드. 공중에 뜬 부분(오버행)이 많거나 속도·방향·서포트를 직접 조정해야 하면 Bambu Studio를 쓰세요."],
      ["서포트(받침)가 뭔가요?", "공중에 떠서 인쇄될 부분을 아래에서 받쳐 주는 임시 구조물입니다. 오버행이 바닥 기준 45°보다 심하면 대체로 필요합니다. STL 바로 업로드는 서포트를 넣지 않으므로, 이런 모델은 Bambu Studio에서 준비하세요."],
      ["예상 출력 시간이 너무 길어요", "모델 크기를 줄이거나 인필(내부 채움)을 낮추세요. 한 시간을 크게 넘는 출력은 신청 전에 관리자와 상의하는 편이 좋습니다."],
      ["출력이 실패하면 다시 할 수 있나요?", "네. 관리자가 실패하거나 중단된 작업을 다시 대기열에 넣을 수 있고, 그 파일은 3일간 보관됩니다. 그 안에 다시 출력하면 됩니다."],
      ["여러 색으로 출력할 수 있나요?", "AMS에 로드된 색을 슬롯 단위로 지정할 수 있습니다. 다만 색을 여러 개 쓰면 설정과 출력 시간이 늘어나니, 단색에 익숙해진 뒤 관리자에게 문의하세요."],
    ],
  },
  {
    group: "STL 바로 업로드",
    items: [
      ["베드에서 모델이 빨갛게 표시돼요", "A1 출력 범위(256 × 256 × 256mm)를 벗어났거나 다른 부품과 겹친 것입니다. 크기 슬라이더로 줄이거나, 드래그·방향키로 위치를 옮기세요. 빨간 부품이 하나라도 있으면 신청 버튼이 눌리지 않습니다."],
      ["모델이 공중에 떠 있어요", "회전한 부품은 자동으로 베드 바닥에 내려앉습니다. 그래도 떠 보이면 페이지를 새로고침하세요. 부품을 감싼 파란 상자는 경계 상자라서 실제 모델보다 커 보일 수 있습니다."],
      ["여러 STL을 한 판에 못 올리겠어요", "한 판 배치는 바이너리 STL만 지원합니다. ASCII STL이 섞여 있으면 신청이 거절되니, 슬라이서나 CAD에서 '바이너리 STL'로 다시 내보내세요."],
      ["승인 대기 줄에 슬라이싱 오류가 떠요", "모델이 닫혀 있지 않거나(non-manifold) 베드를 벗어난 경우가 많습니다. 관리자 메모의 오류 내용을 확인하고, 필요하면 Bambu Studio로 준비해 다시 올리세요."],
    ],
  },
  {
    group: "Bambu Studio",
    items: [
      ["어떤 파일을 올려야 하나요?", "File → Export plate sliced file로 만든 .gcode.3mf 파일입니다. .3mf 프로젝트 파일이나 슬라이싱 전 파일은 받지 않습니다."],
      ["A1이 아닌 다른 프린터로 슬라이싱했어요", "출력 범위와 설정이 맞지 않아 실패할 수 있습니다. 왼쪽 위 프린터 메뉴에서 A1(노즐 0.4mm)로 바꾸고 다시 슬라이싱해 내보내세요."],
      ["필라멘트 색을 어떻게 맞추나요?", "대시보드에서 현재 AMS 슬롯의 색·재질을 확인하고 Bambu Studio에서 같게 설정하세요. 화면 색과 실제 색이 다르면 출력 색도 달라집니다."],
    ],
  },
];

function StepList({ steps, label, heading }: { steps: Step[]; label: string; heading: string }) {
  return (
    <section className="guide-steps" aria-label={label}>
      <h2 className="guide-steps-title">{heading}</h2>
      {steps.map((step, index) => (
        <article className={`guide-step${step.image ? "" : " guide-step-text"}`} key={step.title}>
          <div className="guide-step-copy">
            <span className="guide-number">{index + 1}</span>
            <h2>{step.title}</h2>
            <p>{step.text}</p>
            {step.items.map((item) => <p key={item}>{item}</p>)}
            {step.tip ? <p>{step.tip}</p> : null}
          </div>
          {step.image ? (
            <figure>
              <Image
                src={step.image}
                alt={`${index + 1}단계 ${step.title} 화면`}
                width={1400}
                height={780}
                sizes="(max-width: 900px) 100vw, 54vw"
                loading={index === 0 ? "eager" : "lazy"}
              />
            </figure>
          ) : null}
        </article>
      ))}
    </section>
  );
}

export default function GuidePage() {
  const [tab, setTab] = useState<"stl" | "bambu">("stl");

  return (
    <div className={`page guide-page${tab === "bambu" ? " is-bambu" : ""}`}>
      <header className="page-header">
        <div>
          <h1>3D 출력, 처음부터 차근차근</h1>
        </div>
        <Link href="/upload" className="button button-primary"><Upload size={16} /> 출력 신청</Link>
      </header>

      <section className="guide-choice grid grid-2">
        <button
          type="button"
          className={`card card-body guide-choice-card${tab === "stl" ? " is-active" : ""}`}
          aria-pressed={tab === "stl"}
          onClick={() => setTab("stl")}
        >
          <Box size={23} />
          <h2>STL 바로 업로드</h2>
          <p>STL 파일을 올리면 사이트에서 크기·방향을 확인하고 자동으로 슬라이싱합니다. 설치할 프로그램이 없습니다.</p>
          <p>여러 파일을 한 판에 배치해 한 번에 출력할 수도 있습니다.</p>
          <p>받침이 필요 없는 대부분의 모델에 적합합니다.</p>
        </button>
        <button
          type="button"
          className={`card card-body guide-choice-card${tab === "bambu" ? " is-active" : ""}`}
          aria-pressed={tab === "bambu"}
          onClick={() => setTab("bambu")}
        >
          <FileArchive size={23} />
          <h2>Bambu Studio 사용</h2>
          <p>받침을 직접 넣거나 속도·방향을 세밀하게 조정해야 할 때 사용합니다.</p>
          <p>Bambu Studio에서 슬라이싱해 .gcode.3mf 파일로 제출합니다.</p>
          <p>공중에 뜬 부분이 많은 복잡한 모델에 적합합니다.</p>
        </button>
      </section>

      {tab === "stl" ? (
        <StepList steps={stlSteps} label="STL 바로 업로드 3단계" heading="STL 바로 업로드 — 3단계" />
      ) : (
        <>
          <p className="guide-download">
            <a href="https://bambulab.com/en/download/studio" target="_blank" rel="noopener noreferrer" className="button button-secondary">
              <Download size={15} /> Bambu Studio 받기
            </a>
          </p>
          <StepList steps={bambuSteps} label="Bambu Studio 슬라이싱 단계" heading="Bambu Studio로 슬라이싱하기 — 7단계" />
        </>
      )}

      <section className="faq">
        <h2>자주 묻는 문제</h2>
        {faqGroups.map(({ group, items }) => (
          <div className="faq-group" key={group}>
            <h3>{group}</h3>
            {items.map(([question, answer]) => (
              <details key={question}><summary>{question}</summary><p>{answer}</p></details>
            ))}
          </div>
        ))}
      </section>
      <div className="guide-finish"><h2>준비가 끝났나요?</h2><p>파일을 업로드하고 프린터를 선택하면 관리자 승인 후 차례대로 출력됩니다.</p><Link href="/upload" className="button button-primary">파일 업로드하기</Link></div>
    </div>
  );
}
