// remotion/src/VideoComposition.tsx
import React from "react";
import {
  AbsoluteFill, Audio, interpolate, OffthreadVideo,
  Sequence, spring, useCurrentFrame, useVideoConfig,
} from "remotion";

interface Props {
  title: string;
  sentences: string[];
  keywords: string[];
  audio: string;
  videos: string[];
  duration_s: number;
}

// أنماط بصرية مختلفة لكل جملة
const CAPTION_STYLES = [
  "big_word",      // كلمة ضخمة وسط الشاشة
  "subtitle_bar",  // شريط سفلي ك Netflix
  "top_reveal",    // يظهر من الأعلى
  "split_color",   // خلفية ملونة خلف النص
  "zoom_word",     // الكلمة تكبر عند الظهور
  "shake_emphasis",// اهتزاز خفيف على الكلمة المهمة
] as const;

type CaptionStyle = typeof CAPTION_STYLES[number];

// ألوان الـ pattern interrupt
const ACCENT_COLORS = [
  "#FFE600", // أصفر (الحالي)
  "#FF3366", // أحمر
  "#00F5FF", // سماوي
  "#FF6B35", // برتقالي
  "#7FFF00", // أخضر
];

export const VideoComposition: React.FC<Props> = ({
  sentences, audio, videos, duration_s, title,
}) => {
  const { fps }      = useVideoConfig();
  const totalFrames  = duration_s * fps;
  const framesPerClip = Math.floor(totalFrames / sentences.length);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>

      {sentences.map((sentence, i) => {
        const startFrame = i * framesPerClip;
        const videoSrc   = videos[i] || videos[videos.length - 1];
        const style      = CAPTION_STYLES[i % CAPTION_STYLES.length];
        const accent     = ACCENT_COLORS[i % ACCENT_COLORS.length];

        return (
          <Sequence key={i} from={startFrame} durationInFrames={framesPerClip}>

            {/* خلفية الفيديو */}
            <AbsoluteFill>
              <OffthreadVideo
                src={videoSrc}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                muted
              />
            </AbsoluteFill>

            {/* Pattern interrupt — وميض لوني لأقل من ثانية عند بداية كل جملة */}
            <PatternInterrupt accent={accent} fps={fps} />

            {/* تدرج السواد */}
            <AbsoluteFill style={{
              background: style === "subtitle_bar"
                ? "linear-gradient(to top, rgba(0,0,0,0.92) 0%, rgba(0,0,0,0.0) 40%)"
                : "linear-gradient(to top, rgba(0,0,0,0.75) 0%, rgba(0,0,0,0.1) 55%)",
            }} />

            {/* العنوان — يظهر في الجملة الأولى فقط */}
            {i === 0 && <TitleOverlay title={title} fps={fps} />}

            {/* الكابشن بأسلوبه الخاص */}
            <DynamicCaption
              sentence={sentence}
              style={style}
              accent={accent}
              fps={fps}
              sentenceIndex={i}
              totalSentences={sentences.length}
            />

            {/* Re-hook في آخر جملة — call to action */}
            {i === sentences.length - 1 && (
              <EngagementOverlay fps={fps} accent={accent} />
            )}

          </Sequence>
        );
      })}

      <Audio src={audio} />
    </AbsoluteFill>
  );
};


// ── Pattern Interrupt — وميض لوني يوقف التمرير ──────────────────────────────
const PatternInterrupt: React.FC<{ accent: string; fps: number }> = ({ accent, fps }) => {
  const frame = useCurrentFrame();
  // يظهر لأول 6 فريمات فقط (0.2 ثانية)
  const opacity = interpolate(frame, [0, 2, 6], [0, 0.35, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{
      backgroundColor: accent,
      opacity,
      mixBlendMode: "overlay",
    }} />
  );
};


// ── Title Overlay — يختفي بعد 2 ثانية ────────────────────────────────────────
const TitleOverlay: React.FC<{ title: string; fps: number }> = ({ title, fps }) => {
  const frame = useCurrentFrame();
  const isAr  = /[\u0600-\u06FF]/.test(title);

  const opacity = interpolate(frame, [0, 8, fps * 1.8, fps * 2.2], [0, 1, 1, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{
      justifyContent: "flex-start",
      alignItems: "center",
      paddingTop: 120,
      opacity,
    }}>
      <div style={{
        background: "rgba(0,0,0,0.65)",
        backdropFilter: "blur(12px)",
        border: "1px solid rgba(255,255,255,0.25)",
        borderRadius: 20,
        padding: "16px 36px",
        maxWidth: 900,
        direction: isAr ? "rtl" : "ltr",
      }}>
        <span style={{
          color: "#fff",
          fontSize: isAr ? 40 : 36,
          fontWeight: 800,
          fontFamily: isAr ? "'Noto Naskh Arabic',serif" : "Arial Black, sans-serif",
          textAlign: "center",
          textShadow: "0 2px 12px rgba(0,0,0,0.8)",
        }}>
          {title}
        </span>
      </div>
    </AbsoluteFill>
  );
};


// ── Dynamic Caption — 6 أنماط مختلفة ─────────────────────────────────────────
const DynamicCaption: React.FC<{
  sentence: string;
  style: CaptionStyle;
  accent: string;
  fps: number;
  sentenceIndex: number;
  totalSentences: number;
}> = ({ sentence, style, accent, fps, sentenceIndex, totalSentences }) => {
  const frame = useCurrentFrame();
  const isAr  = /[\u0600-\u06FF]/.test(sentence);
  const font  = isAr ? "'Noto Naskh Arabic',serif" : "Arial Black, sans-serif";

  switch (style) {

    case "big_word":
      return <BigWordCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    case "subtitle_bar":
      return <SubtitleBarCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    case "top_reveal":
      return <TopRevealCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    case "split_color":
      return <SplitColorCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    case "zoom_word":
      return <ZoomWordCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    case "shake_emphasis":
      return <ShakeEmphasisCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;

    default:
      return <BigWordCaption sentence={sentence} accent={accent} fps={fps} font={font} isAr={isAr} />;
  }
};


// ── نمط 1: كلمة ضخمة (الحالي محسّن) ─────────────────────────────────────────
const BigWordCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame   = useCurrentFrame();
  const opacity = interpolate(frame, [0, 6], [0, 1], { extrapolateRight: "clamp" });
  const scale   = spring({ frame, fps, config: { damping: 100, stiffness: 300 } });
  const s       = interpolate(scale, [0, 1], [0.85, 1]);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 140 }}>
      <div style={{
        opacity, transform: `scale(${s})`,
        color: accent, fontSize: isAr ? 80 : 76,
        fontWeight: 900, fontFamily: font,
        textAlign: "center", direction: isAr ? "rtl" : "ltr",
        paddingLeft: 48, paddingRight: 48, lineHeight: 1.2,
        textShadow: `0 0 40px ${accent}88, 0 4px 20px rgba(0,0,0,1)`,
        maxWidth: 980,
      }}>
        {sentence}
      </div>
    </AbsoluteFill>
  );
};


// ── نمط 2: شريط سفلي ك Netflix ───────────────────────────────────────────────
const SubtitleBarCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame   = useCurrentFrame();
  const opacity = interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  const slideY  = spring({ frame, fps, config: { damping: 120, stiffness: 280 } });
  const y       = interpolate(slideY, [0, 1], [40, 0]);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end" }}>
      <div style={{
        opacity, transform: `translateY(${y}px)`,
        background: "rgba(0,0,0,0.88)",
        borderTop: `4px solid ${accent}`,
        padding: "28px 52px 48px",
        width: "100%",
        direction: isAr ? "rtl" : "ltr",
      }}>
        <div style={{
          color: "#fff", fontSize: isAr ? 52 : 48,
          fontWeight: 800, fontFamily: font,
          textAlign: isAr ? "right" : "left",
          lineHeight: 1.35,
          textShadow: "0 2px 8px rgba(0,0,0,0.9)",
        }}>
          <span style={{ color: accent, marginInlineEnd: 12 }}>▌</span>
          {sentence}
        </div>
      </div>
    </AbsoluteFill>
  );
};


// ── نمط 3: يظهر من الأعلى ────────────────────────────────────────────────────
const TopRevealCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame   = useCurrentFrame();
  const opacity = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const slideY  = spring({ frame, fps, config: { damping: 90, stiffness: 250 } });
  const y       = interpolate(slideY, [0, 1], [-50, 0]);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-start", alignItems: "center", paddingTop: 200 }}>
      <div style={{
        opacity, transform: `translateY(${y}px)`,
        background: `linear-gradient(135deg, rgba(0,0,0,0.85), ${accent}22)`,
        border: `2px solid ${accent}55`,
        borderRadius: 20,
        padding: "24px 48px",
        maxWidth: 920,
        direction: isAr ? "rtl" : "ltr",
        backdropFilter: "blur(8px)",
      }}>
        <div style={{
          color: "#fff", fontSize: isAr ? 54 : 50,
          fontWeight: 900, fontFamily: font,
          textAlign: "center", lineHeight: 1.3,
          textShadow: "0 2px 12px rgba(0,0,0,0.9)",
        }}>
          {sentence}
        </div>
      </div>
    </AbsoluteFill>
  );
};


// ── نمط 4: خلفية ملونة خلف النص ──────────────────────────────────────────────
const SplitColorCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame   = useCurrentFrame();
  const opacity = interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  const scaleX  = spring({ frame, fps, config: { damping: 100, stiffness: 200 } });
  const sx      = interpolate(scaleX, [0, 1], [0.3, 1]);

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{
        opacity,
        background: accent,
        transform: `scaleX(${sx})`,
        transformOrigin: isAr ? "right center" : "left center",
        padding: "32px 56px",
        maxWidth: 960,
        direction: isAr ? "rtl" : "ltr",
      }}>
        <div style={{
          color: "#000", fontSize: isAr ? 64 : 60,
          fontWeight: 900, fontFamily: font,
          textAlign: "center", lineHeight: 1.2,
        }}>
          {sentence}
        </div>
      </div>
    </AbsoluteFill>
  );
};


// ── نمط 5: zoom عند الظهور ────────────────────────────────────────────────────
const ZoomWordCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame  = useCurrentFrame();
  const zoom   = spring({ frame, fps, config: { damping: 60, stiffness: 400, mass: 0.8 } });
  const scale  = interpolate(zoom, [0, 1], [1.4, 1]);
  const opacity = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 160 }}>
      <div style={{
        opacity, transform: `scale(${scale})`,
        color: "#fff", fontSize: isAr ? 72 : 68,
        fontWeight: 900, fontFamily: font,
        textAlign: "center", direction: isAr ? "rtl" : "ltr",
        paddingLeft: 48, paddingRight: 48, lineHeight: 1.25,
        textShadow: `0 0 60px ${accent}99, 0 4px 24px rgba(0,0,0,1)`,
        maxWidth: 980,
      }}>
        {sentence}
      </div>
    </AbsoluteFill>
  );
};


// ── نمط 6: اهتزاز على الكلمة ─────────────────────────────────────────────────
const ShakeEmphasisCaption: React.FC<{
  sentence: string; accent: string; fps: number; font: string; isAr: boolean;
}> = ({ sentence, accent, fps, font, isAr }) => {
  const frame   = useCurrentFrame();
  const opacity = interpolate(frame, [0, 6], [0, 1], { extrapolateRight: "clamp" });

  // اهتزاز خفيف في الـ 8 فريمات الأولى
  const shakeX = frame < 8
    ? Math.sin(frame * 3.5) * interpolate(frame, [0, 8], [5, 0], { extrapolateRight: "clamp" })
    : 0;

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 150 }}>
      <div style={{
        opacity, transform: `translateX(${shakeX}px)`,
        color: accent, fontSize: isAr ? 76 : 72,
        fontWeight: 900, fontFamily: font,
        textAlign: "center", direction: isAr ? "rtl" : "ltr",
        paddingLeft: 48, paddingRight: 48, lineHeight: 1.2,
        textShadow: `0 0 40px ${accent}77, 0 4px 20px rgba(0,0,0,1), 3px 3px 0 rgba(0,0,0,0.8)`,
        maxWidth: 980,
      }}>
        {sentence}
      </div>
    </AbsoluteFill>
  );
};


// ── Engagement Overlay — CTA في آخر 2 ثانية ──────────────────────────────────
const EngagementOverlay: React.FC<{ fps: number; accent: string }> = ({ fps, accent }) => {
  const frame        = useCurrentFrame();
  const SHOW_FROM    = fps * 1.5;  // يظهر بعد 1.5 ثانية من الجملة الأخيرة
  const opacity      = interpolate(frame, [SHOW_FROM, SHOW_FROM + 12], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const bounceSpring = spring({ frame: Math.max(0, frame - SHOW_FROM), fps,
    config: { damping: 80, stiffness: 300 } });
  const bounceY = interpolate(bounceSpring, [0, 1], [30, 0]);

  if (frame < SHOW_FROM) return null;

  return (
    <AbsoluteFill style={{
      justifyContent: "center", alignItems: "center",
      opacity, transform: `translateY(${bounceY}px)`,
    }}>
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 20,
      }}>
        {/* حفظ الفيديو */}
        <div style={{
          background: accent,
          borderRadius: 50, padding: "20px 48px",
          display: "flex", alignItems: "center", gap: 16,
          boxShadow: `0 0 40px ${accent}88`,
        }}>
          <span style={{ fontSize: 48 }}>🔖</span>
          <span style={{
            color: "#000", fontSize: 44, fontWeight: 900,
            fontFamily: "Arial Black, sans-serif",
          }}>
            احفظ الفيديو
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};
