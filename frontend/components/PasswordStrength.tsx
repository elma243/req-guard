import { passwordRules, passwordStrength } from "@/lib/authRules";

const BAR_COLOURS = ["bg-rose-500", "bg-rose-500", "bg-amber-400", "bg-lime-400", "bg-emerald-400"];
const TEXT_COLOURS = ["text-rose-400", "text-rose-400", "text-amber-300", "text-lime-300", "text-emerald-300"];
const BARS = [1, 2, 3, 4];

// Live strength meter and checklist of the password rules. Pass `email` to include "Not your email".
export default function PasswordStrength({ password, email = "" }: { password: string; email?: string }) {
  if (!password) return null;
  const { score, label } = passwordStrength(password, email);

  return (
    <div className="mt-2.5">
      <div className="flex items-center gap-3">
        <div className="flex flex-1 gap-1.5" aria-hidden="true">
          {BARS.map((bar) => (
            <span
              key={bar}
              className={`h-1.5 flex-1 rounded-full transition ${bar <= score ? BAR_COLOURS[score] : "bg-white/10"}`}
            />
          ))}
        </div>
        <span aria-live="polite" className={`w-16 text-right text-xs font-medium ${TEXT_COLOURS[score]}`}>
          {label}
        </span>
      </div>
      <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {passwordRules(password, email).map((rule) => (
          <li key={rule.label} className={rule.met ? "text-emerald-300" : "text-zinc-500"}>
            {rule.met ? "✓" : "○"} {rule.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
