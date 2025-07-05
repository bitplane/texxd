# texthed

A hex viewer, soon to be editor for textual.

## todo

- [ ] Adapt to new widget-based column object thing.

- [x] Break hex and ascii views into generic data column viewers
  - [x] address viewer column
  - [x] hex view with style choices (like xdd, od, hexdump -C)
  - [x] ascii view
  - [ ] support click select
  - [ ] support drag-select
- [ ] Support highlighter for byte ranges
  - [x] byte ranges go through highlighters in order before rendering
  - [ ] find can be a highlighter
  - [ ] unsaved edits as a highlighter
  - [ ] binary diff tool as a highlighter
  - [ ] view's own highlighter applied last
- [ ] Design better cursor nav
  - [ ] vim style normal and edit modes (i, o, a to enter edit, esc = normal)
  - [x] has current position = highlight by default
    - [x] base class with home, end, page up/down, cursor, ctrl+home/end support
  - [x] decides which part of the file should be visible
  - [x] can do large jumps without scrolling slowly + reading all data
  - [ ] support editing by default
- [x] hex_file.py
  - [x] memoryview over file as a cache
  - [x] store byte ranges of unsaved edits
  - [x] refresh/reload support
  - [x] save/commit support

