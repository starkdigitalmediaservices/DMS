import type { ReviewBlockType, ReviewStatus } from "@/types";

// Full class strings (not built from fragments) so Tailwind's scanner keeps them.
export const BLOCK_TYPE_STYLE: Record<ReviewBlockType, {
  border: string; activeBorder: string; activeFill: string; hoverFill: string; labelBg: string; chip: string;
}> = {
  heading: {
    border: "border-purple-500", activeBorder: "border-purple-700", activeFill: "bg-purple-400/25",
    hoverFill: "bg-purple-300/20", labelBg: "bg-purple-700", chip: "bg-purple-50 text-purple-800 border-purple-200",
  },
  paragraph: {
    border: "border-blue-500", activeBorder: "border-blue-700", activeFill: "bg-blue-400/25",
    hoverFill: "bg-blue-300/20", labelBg: "bg-blue-700", chip: "bg-blue-50 text-blue-800 border-blue-200",
  },
  table: {
    border: "border-emerald-600", activeBorder: "border-emerald-800", activeFill: "bg-emerald-400/25",
    hoverFill: "bg-emerald-300/20", labelBg: "bg-emerald-800", chip: "bg-emerald-50 text-emerald-800 border-emerald-200",
  },
  image: {
    border: "border-orange-500", activeBorder: "border-orange-700", activeFill: "bg-orange-400/25",
    hoverFill: "bg-orange-300/20", labelBg: "bg-orange-700", chip: "bg-orange-50 text-orange-800 border-orange-200",
  },
};

export const STATUS_LABEL: Record<ReviewStatus, string> = {
  MACHINE_EXTRACTED: "Machine-extracted",
  EDITED: "Edited",
  VERIFIED: "Verified",
};

export const STATUS_CHIP: Record<ReviewStatus, string> = {
  MACHINE_EXTRACTED: "bg-[#f0f4f9] text-[#444746] border-[#e1e3e1]",
  EDITED: "bg-amber-50 text-amber-900 border-amber-300",
  VERIFIED: "bg-green-50 text-green-800 border-green-300",
};
