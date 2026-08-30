import { TopNav } from "./top-nav";

/**
 * Application shell: sticky top nav + one centered content column.
 * Desktop-first; the 1320px column with 32px gutters is the 1440px target.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50">
      <TopNav />
      <main className="mx-auto w-full max-w-[1320px] flex-1 px-4 pb-16 pt-7 sm:px-8">
        {children}
      </main>
    </div>
  );
}
