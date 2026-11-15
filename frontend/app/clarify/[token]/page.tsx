import type { Metadata } from "next";
import ClarifyForm from "./ClarifyForm";

// The client's page for a clarification link (http://localhost:3000/clarify/<token>). No login: the token in the
// address is the only key, so it must not travel to other sites in a Referer header, and search engines must skip it.
export const metadata: Metadata = {
  title: "Questions from the team",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default async function ClarifyPage({ params }: PageProps<"/clarify/[token]">) {
  const { token } = await params;
  return <ClarifyForm token={token} />;
}
