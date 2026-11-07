// Live input checks for the auth pages. Same rules and messages as the backend (backend/app/schemas/auth.py),
// which stays the source of truth: these only give the answer sooner.

export const NAME_MIN = 2;
export const NAME_MAX = 60;
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 128;
export const OTP_LENGTH = 6;

// Counts characters as the backend does (an emoji is one, not two)
const length = (text: string) => [...text].length;

const hasLetter = (text: string) => /\p{L}/u.test(text);
const hasNumber = (text: string) => /\p{Nd}/u.test(text);
const equalsEmail = (password: string, email: string) =>
  email.trim() !== "" && password.trim().toLowerCase() === email.trim().toLowerCase();

export function nameError(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return "Name is required.";
  if (length(trimmed) < NAME_MIN || length(trimmed) > NAME_MAX) {
    return `Name must be ${NAME_MIN} to ${NAME_MAX} characters.`;
  }
}

export function emailError(email: string): string | undefined {
  const trimmed = email.trim();
  if (!trimmed) return "Email is required.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) return "Enter a valid email address.";
}

// `email` is empty on the reset page: there the backend does that one check
export function passwordError(password: string, email = ""): string | undefined {
  if (length(password) < PASSWORD_MIN) return `Password must be at least ${PASSWORD_MIN} characters.`;
  if (length(password) > PASSWORD_MAX) return `Password must be at most ${PASSWORD_MAX} characters.`;
  if (!hasLetter(password)) return "Password must contain a letter.";
  if (!hasNumber(password)) return "Password must contain a number.";
  if (equalsEmail(password, email)) return "Password must not be the same as your email.";
}

export function confirmError(password: string, confirm: string): string | undefined {
  if (confirm !== password) return "Passwords do not match.";
}

export type PasswordRule = { label: string; met: boolean };

export function passwordRules(password: string, email = ""): PasswordRule[] {
  const rules = [
    { label: `At least ${PASSWORD_MIN} characters`, met: length(password) >= PASSWORD_MIN },
    { label: "A letter", met: hasLetter(password) },
    { label: "A number", met: hasNumber(password) },
  ];
  if (email.trim()) rules.push({ label: "Not your email", met: !equalsEmail(password, email) });
  return rules;
}

export type PasswordStrength = { score: 0 | 1 | 2 | 3 | 4; label: string };

const STRENGTH_LABELS = ["Too weak", "Weak", "Fair", "Good", "Strong"];

// A rough guide for the user. A password that breaks a rule never scores above "Weak".
export function passwordStrength(password: string, email = ""): PasswordStrength {
  if (!password) return { score: 0, label: "" };
  let score = 0;
  if (length(password) >= PASSWORD_MIN) score++;
  if (length(password) >= 12) score++;
  if (hasLetter(password) && hasNumber(password)) score++;
  if (/\p{Lu}/u.test(password) && /\p{Ll}/u.test(password)) score++;
  if (/[^\p{L}\p{Nd}]/u.test(password)) score++;
  const capped = (passwordError(password, email) ? Math.min(score, 1) : Math.min(score, 4)) as PasswordStrength["score"];
  return { score: capped, label: STRENGTH_LABELS[capped] };
}
