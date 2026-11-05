// Word-level difference between two texts, for the version history.

export type DiffPart = { kind: "same" | "added" | "removed"; text: string };

// Above this many cells, comparing word by word is not worth the time: show old and new whole.
const MAX_CELLS = 4_000_000;

export function wordDiff(before: string, after: string): DiffPart[] {
  const a = before.split(/(\s+)/);
  const b = after.split(/(\s+)/);
  if (a.length * b.length > MAX_CELLS) {
    return [
      { kind: "removed", text: before },
      { kind: "added", text: after },
    ];
  }

  // lcs[i + j * width] = length of the longest common sequence of a[i:] and b[j:]
  const width = a.length + 1;
  const lcs = new Uint16Array(width * (b.length + 1));
  for (let j = b.length - 1; j >= 0; j--) {
    for (let i = a.length - 1; i >= 0; i--) {
      lcs[i + j * width] =
        a[i] === b[j]
          ? lcs[i + 1 + (j + 1) * width] + 1
          : Math.max(lcs[i + 1 + j * width], lcs[i + (j + 1) * width]);
    }
  }

  const parts: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    if (!text) return;
    const last = parts[parts.length - 1];
    if (last?.kind === kind) last.text += text;
    else parts.push({ kind, text });
  };

  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (j >= b.length || (i < a.length && lcs[i + 1 + j * width] >= lcs[i + (j + 1) * width])) {
      push("removed", a[i++]);
    } else {
      push("added", b[j++]);
    }
  }
  return parts;
}
