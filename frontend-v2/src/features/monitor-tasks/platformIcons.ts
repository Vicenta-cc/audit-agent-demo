import type { MonitorTask } from "../../types/jobs";

const platformIconMap = {
  local: new URL("./assets/platform-local-video.svg", import.meta.url).href,
  generic: new URL("./assets/platform-generic.svg", import.meta.url).href
} as const;

export type PlatformIconKey = keyof typeof platformIconMap;

export function getPlatformIcon(task: MonitorTask) {
  const key = resolvePlatformIconKey(task);
  return {
    key,
    src: platformIconMap[key]
  };
}

function resolvePlatformIconKey(task: MonitorTask): PlatformIconKey {
  if (task.raw.input_type === "local_video" || task.source === "本地视频") {
    return "local";
  }

  return "generic";
}
