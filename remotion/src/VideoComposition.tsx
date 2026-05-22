import React from "react";
import {
  AbsoluteFill,
  Audio,
  interpolate,
  OffthreadVideo,
  Sequence,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

interface Props {
  title: string;
  sentences: string[];
  keywords: string[];
  audio: string;
  videos: string[];
  duration_s: number;
}

export const VideoComposition: React.FC<Props> = ({
  sentences,
  audio,
  videos,
  duration_s,
}) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const totalFrames = duration_s * fps;
  const framesPerClip = Math.floor(totalFrames / sentences.length);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>

      {/* ── Video clips — one per sentence ── */}
      {sentences.map((sentence, i) => {
        const startFrame = i * framesPerClip;
        const videoSrc = videos[i] || videos[videos.length - 1];

        return (
          <Sequence
            key={i}
            from={startFrame}
            durationInFrames={framesPerClip}
          >
            {/* Background video clip */}
            <AbsoluteFill>
              <OffthreadVideo
                src={videoSrc}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                muted
              />
            </AbsoluteFill>

            {/* Dark overlay for readability */}
            <AbsoluteFill
              style={{ background: "linear-gradient(to top, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0.15) 60%)" }}
            />

            {/* Sentence caption */}
            <Caption sentence={sentence} fps={fps} />
          </Sequence>
        );
      })}

      {/* ── Voiceover audio ── */}
      <Audio src={audio} />

    </AbsoluteFill>
  );
};


/* ── Caption component with animated entrance ── */
const Caption: React.FC<{ sentence: string; fps: number }> = ({ sentence, fps }) => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  const translateY = spring({ frame, fps, config: { damping: 80, stiffness: 200 } });
  const y = interpolate(translateY, [0, 1], [30, 0]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems:     "center",
        paddingBottom:  120,
      }}
    >
      <div
        style={{
          opacity,
          transform:      `translateY(${y}px)`,
          color:          "#ffffff",
          fontSize:        52,
          fontWeight:      800,
          fontFamily:      "Arial Black, sans-serif",
          textAlign:       "center",
          paddingLeft:     48,
          paddingRight:    48,
          lineHeight:      1.3,
          textShadow:      "0 2px 12px rgba(0,0,0,0.9)",
          maxWidth:        980,
        }}
      >
        {sentence}
      </div>
    </AbsoluteFill>
  );
};
