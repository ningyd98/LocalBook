import { useI18n } from "../i18n";

/**
 * Heading toolbar (companion to the Ctrl-1…6 shortcuts).
 *
 * Each button toggles the heading level on the lines the selection touches:
 * pressing the level a line already has returns it to body text. The buttons
 * are plain text labels (`H1`…`H6`, `¶`) so no new icon assets are needed.
 */
export function HeadingToolbar({ onHeading, disabled = false }: { onHeading: (level: number) => void; disabled?: boolean }) {
  const { t } = useI18n();
  const labels: Record<number, string> = {
    1: t.heading.h1,
    2: t.heading.h2,
    3: t.heading.h3,
    4: t.heading.h4,
    5: t.heading.h5,
    6: t.heading.h6,
    0: t.heading.plain,
  };
  return <div className="heading-toolbar" role="group" aria-label={t.heading.toolbar}>
    {[1, 2, 3, 4, 5, 6].map((level) => (
      <button
        key={level}
        type="button"
        className="heading-button"
        title={labels[level]}
        aria-label={labels[level]}
        data-level={level}
        disabled={disabled}
        onClick={() => onHeading(level)}
      >
        H{level}
      </button>
    ))}
    <button
      type="button"
      className="heading-button heading-plain"
      title={labels[0]}
      aria-label={labels[0]}
      data-level={0}
      disabled={disabled}
      onClick={() => onHeading(0)}
    >
      ¶
    </button>
  </div>;
}
