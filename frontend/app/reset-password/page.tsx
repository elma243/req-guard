import type { Metadata } from "next";
import ResetPasswordForm from "./ResetPasswordForm";

// ReqGuard — Reset password page, opened from the emailed link (http://localhost:3000/reset-password?token=...)

// The address holds the reset token, so it must not travel to other sites in a Referer header
export const metadata: Metadata = { title: "Reset password · ReqGuard", referrer: "no-referrer" };

export default async function ResetPasswordPage({ searchParams }: PageProps<"/reset-password">) {
  const { token } = await searchParams;
  return <ResetPasswordForm token={typeof token === "string" ? token : ""} />;
}
