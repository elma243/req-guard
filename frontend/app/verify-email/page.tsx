import VerifyEmailForm from "./VerifyEmailForm";

// ReqGuard — Verify email page (http://localhost:3000/verify-email)
// ?email= fills in the address; ?sent=1 means a code was just sent, so "Resend code" starts on its cooldown.

export default async function VerifyEmailPage({ searchParams }: PageProps<"/verify-email">) {
  const { email, sent } = await searchParams;
  return <VerifyEmailForm initialEmail={typeof email === "string" ? email : ""} justSent={sent === "1"} />;
}
