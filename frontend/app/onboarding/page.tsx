import { OnboardingView } from "@/components/onboarding-view";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

export default function OnboardingPage() {
  return (
    <>
      <PageHeader
        title="Choose your merchant data"
        subtitle="Bring your own payment history, or explore Revenue Autopilot with the TechBazaar demo dataset."
      />
      <OnboardingView />
    </>
  );
}
