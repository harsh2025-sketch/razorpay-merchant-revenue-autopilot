import { redirect } from "next/navigation";

/** The product entry point is always the Overview. */
export default function RootPage() {
  redirect("/overview");
}
