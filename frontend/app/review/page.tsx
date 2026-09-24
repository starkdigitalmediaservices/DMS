"use client";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

// The review screen now lives inside the Workbench. Old /review links
// (bookmarks, earlier builds) keep working by forwarding their parameters.
function Forward() {
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    const next = new URLSearchParams(params.toString());
    if (!next.get("from")) next.set("from", "documents");
    router.replace(`/workbench?${next.toString()}`);
  }, [params, router]);
  return <p className="p-8 text-sm text-[#5f6368]">Opening in the Workbench…</p>;
}

export default function ReviewRedirectPage() {
  return (
    <Suspense fallback={null}>
      <Forward />
    </Suspense>
  );
}
