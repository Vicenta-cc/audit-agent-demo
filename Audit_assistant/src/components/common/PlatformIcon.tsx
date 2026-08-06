import React from "react";

import xiaohongshuSvg from "../../features/monitor-tasks/assets/platform-xiaohongshu.svg";
import douyinSvg from "../../features/monitor-tasks/assets/platform-douyin.svg";
import kuaishouSvg from "../../features/monitor-tasks/assets/platform-kuaishou.svg";
import weiboSvg from "../../features/monitor-tasks/assets/platform-weibo.svg";
import wechatSvg from "../../features/monitor-tasks/assets/platform-wechat.svg";
import genericSvg from "../../features/monitor-tasks/assets/platform-generic.svg";

interface PlatformIconProps {
  platform?: string;
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function getPlatformMeta(platformKey?: string) {
  const normalized = (platformKey || "").toLowerCase().trim();

  if (normalized.includes("小红书") || normalized === "xhs" || normalized === "xiaohongshu" || normalized === "red") {
    return { name: "小红书", icon: xiaohongshuSvg, color: "#ff2442", code: "xhs" };
  }
  if (normalized.includes("抖音") || normalized === "dy" || normalized === "douyin") {
    return { name: "抖音", icon: douyinSvg, color: "#161823", code: "dy" };
  }
  if (normalized.includes("快手") || normalized === "ks" || normalized === "kuaishou") {
    return { name: "快手", icon: kuaishouSvg, color: "#ff6d00", code: "ks" };
  }
  if (normalized.includes("微博") || normalized === "wb" || normalized === "weibo") {
    return { name: "微博", icon: weiboSvg, color: "#e6162d", code: "wb" };
  }
  if (normalized.includes("微信") || normalized === "wx" || normalized === "wechat") {
    return { name: "微信", icon: wechatSvg, color: "#07c160", code: "wx" };
  }

  return { name: platformKey || "全平台", icon: genericSvg, color: "#64748b", code: "generic" };
}

export const PlatformIcon: React.FC<PlatformIconProps> = ({
  platform,
  size = 18,
  className = "",
  style
}) => {
  const meta = getPlatformMeta(platform);

  return (
    <img
      src={meta.icon}
      alt={meta.name}
      className={`platform-icon-img ${className}`}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        objectFit: "contain",
        borderRadius: size >= 24 ? "4px" : "2px",
        flexShrink: 0,
        display: "inline-block",
        verticalAlign: "middle",
        ...style
      }}
    />
  );
};
