import { Composition } from "remotion";
import { VideoComposition } from "./VideoComposition";

export const Root: React.FC = () => {
  return (
    <Composition
      id="VideoComposition"
      component={VideoComposition}
      durationInFrames={2400}   // overridden via props
      fps={30}
      width={1080}
      height={1920}             // 9:16 vertical (Shorts / Reels)
      defaultProps={{
        title: "",
        sentences: [],
        keywords: [],
        audio: "",
        videos: [],
        duration_s: 60,
      }}
    />
  );
};
