# texthed

A hex viewer, soon to be editor for textual.

## todo

- [ ] Break hex and ascii views into generic data column viewers
  - [ ] address viewer column
  - [ ] hex view with style choices (like xdd, od, hexdump -C)
  - [ ] ascii view
  - [ ] unicode view?
  - [ ] support click select
- [ ] Support highlighter for byte ranges
  - [ ] byte ranges go through highlighters in order before rendering
  - [ ] find can be a highlighter
  - [ ] unsaved edits as a highlighter
  - [ ] binary diff tool as a highlighter
  - [ ] view's own highlighter applied last
- [ ] Design better cursor nav
  - [ ] vim style normal and edit modes (i, o, a to enter edit, esc = normal)
  - [ ] has current position = highlight by default
    - [ ] base class with home, end, page up/down, cursor, ctrl+home/end support
  - [ ] decides which part of the file should be visible
  - [ ] can do large jumps without scrolling slowly + reading all data
  - [ ] support editing by default
- [ ] hex_file.py
  - [ ] memoryview over file as a cache
  - [ ] store byte ranges of unsaved edits
  - [ ] refresh/reload support
  - [ ] save/commit support

