"""Undo history shared by every buffer in a document.

A buffer holding derived data (like a decompressed stream) keeps its own
edits, but shares its history with the buffer it came from, so one undo step
can span several buffers: committing a derived buffer's edits back into its
parent is one step, and undoing it puts both back.

Edits happen in transactions: an edit and everything it triggers (like a
container fixing up its headers after a file inside it changed size) is one
undo step, and if any part fails the whole thing is rolled back.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

if TYPE_CHECKING:
    from .buffer import Buffer, Change


@dataclass
class _Step:
    """One undoable transaction: each buffer it touched as it was before, and the changes it made, in order."""

    states: dict["Buffer", Any] = field(default_factory=dict)
    changes: list[tuple["Buffer", "Change"]] = field(default_factory=list)


class History:
    """Undo and redo steps, and the transaction in progress."""

    def __init__(self) -> None:
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        # the transaction in progress: how deep, its step, the redo stack it replaced,
        # and work to do before it finishes
        self._depth = 0
        self._step: Optional[_Step] = None
        self._redo_before: list[_Step] = []
        self._deferred: dict[Any, tuple[int, Callable[[], None]]] = {}
        self._listeners: list[Callable[[], None]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` once things have settled after each transaction, undo, redo or save."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _settled(self) -> None:
        for listener in list(self._listeners):
            listener()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group edits into one undo step. If anything raises, they're all rolled back.

        Transactions nest; the outermost one runs deferred work before it
        finishes, and that work's edits are part of the same step.
        """
        self._depth += 1
        try:
            yield
            if self._depth == 1:
                self._run_deferred()
        except BaseException:
            if self._depth == 1:
                self._rollback()
            raise
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._step = None
                self._deferred.clear()
                self._settled()

    def defer(self, key: Any, priority: int, work: Callable[[], None]) -> None:
        """Run ``work`` before the current transaction finishes, highest ``priority`` first.

        Work with the same ``key`` is only run once. Outside a transaction (like
        during undo, which restores a state that already had the work done) it's
        ignored.
        """
        if self._depth:
            self._deferred[key] = (priority, work)

    def _run_deferred(self) -> None:
        while self._deferred:
            key = max(self._deferred, key=lambda k: self._deferred[k][0])
            _, work = self._deferred.pop(key)
            work()

    def record(self, buffer: "Buffer", change: Optional["Change"] = None) -> None:
        """Note an edit to ``buffer`` about to happen, or with no change, that the step touches it.

        The first time a step touches a buffer, its state is kept so undo can
        put it back. Only call this inside a transaction.
        """
        if self._step is None:
            self._step = _Step()
            self._undo.append(self._step)
            self._redo_before = self._redo
            self._redo = []
        if buffer not in self._step.states:
            self._step.states[buffer] = buffer._begin_step()
        if change is not None:
            self._step.changes.append((buffer, change))

    def _rollback(self) -> None:
        step = self._step
        if step is None:
            return
        self._undo.pop()
        self._redo = self._redo_before
        self._restore(step, undoing=True)

    def _restore(self, step: _Step, undoing: bool) -> _Step:
        """Put the buffers back as ``step`` has them and announce it. Returns the step to go back again."""
        back = _Step({buffer: buffer._state() for buffer in step.states}, step.changes)
        for buffer, state in step.states.items():
            buffer._restore(state)
        if undoing:
            for buffer, change in reversed(step.changes):
                buffer._emit(change.inverse())
        else:
            for buffer, change in step.changes:
                buffer._emit(change)
        return back

    @staticmethod
    def _where(step: _Step, undoing: bool) -> tuple["Buffer", Optional["Change"]]:
        if not step.changes:
            return next(iter(step.states)), None
        buffer, change = step.changes[0]
        return buffer, change.inverse() if undoing else change

    def undo(self) -> Optional[tuple["Buffer", Optional["Change"]]]:
        """Undo the last step.

        Returns the buffer and change it started with, undone (None for a step
        that only touched buffers), or None if there was nothing to undo.
        """
        if not self._undo:
            return None
        step = self._undo.pop()
        self._redo.append(self._restore(step, undoing=True))
        self._settled()
        return self._where(step, undoing=True)

    def redo(self) -> Optional[tuple["Buffer", Optional["Change"]]]:
        """Redo the last undone step. Returns where, like undo()."""
        if not self._redo:
            return None
        step = self._redo.pop()
        self._undo.append(self._restore(step, undoing=False))
        self._settled()
        return self._where(step, undoing=False)

    def clear(self) -> None:
        """Forget every step, like after saving."""
        self._undo.clear()
        self._redo.clear()
        self._settled()
