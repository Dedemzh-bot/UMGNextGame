# Text component content and granularity

These rules were explicitly supplied by the project owner on 2026-07-23. Treat them as authoritative.

## Text-only content

- Put only readable text in a `TextBlock`: Chinese characters, letters, numbers, whitespace between words, punctuation, and common textual symbols.
- Do not put icons, pictographs, arrows, geometric markers, controller glyphs, or decorative line characters in a `TextBlock`.
- Represent an icon or marker with a project `GameImage` component even when a Unicode character visually resembles it.
- Represent a horizontal rule, underline, divider, frame segment, or other decoration with a project `GameImage` component.
- Do not construct a divider by repeating hyphens, underscores, box-drawing glyphs, or similar characters.

Example:

```text
PanelTaskHeader
├─ TxtTaskTitle              text: 任务
└─ ImgTaskTitleSeparator     thin horizontal image
```

## One visual text block per component

- Create one `TextBlock` for every independently bounded text block recognized in the reference image.
- Split titles, descriptions, objective rows, player names, category names, item names, counts, timers, and navigation labels into separate components when they occupy separate visual bounds.
- Do not combine a menu, legend, task group, or row collection into one multiline `TextBlock`.
- Do not use tabs, manual line breaks, or repeated spaces to align multiple labels inside one component.
- Keep a continuous paragraph in one `TextBlock`; store its source text without manual line breaks and enable `autoWrap` when it must wrap within its bounds.
- Use separate components when different lines need independent layout, styling, visibility, data binding, or replacement.
- Use a positive even integer for every explicit font size.
- Set `Wrap Text At` to a concrete positive value whenever the text is intended to wrap; do not leave it at `0`.
- Plan expandable text bounds and related backgrounds for longer localized strings as defined in `common-widget-rules.md`.

## Validation expectations

- Reject tab and newline characters in authored TextBlock content.
- Reject repeated spaces used as layout.
- Reject icon-like or decorative Unicode glyphs.
- Reject repeated-character separators.
- Allow visual wrapping only through TextBlock layout such as `autoWrap`, not through manually authored line breaks.
