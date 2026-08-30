"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LoadingButton } from "./loading-button";

/**
 * Server-rendered pages need one client affordance when their initial read
 * fails: a single Retry that re-runs the server fetch.
 */
export function RetryRefresh({ label = "Retry" }: { label?: string }) {
  const router = useRouter();
  const [retrying, setRetrying] = useState(false);

  return (
    <LoadingButton
      variant="outline"
      loading={retrying}
      loadingLabel="Retrying…"
      onClick={() => {
        setRetrying(true);
        router.refresh();
        // The refresh re-renders the server component; the state clears on
        // the next navigation either way.
        setTimeout(() => setRetrying(false), 2000);
      }}
    >
      {label}
    </LoadingButton>
  );
}
