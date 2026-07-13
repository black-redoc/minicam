/**
 * view-cycle.js — pure bounce/ping-pong state machine for the 3-mode
 * view toggle (single → grid → stack → grid → single → ...).
 *
 * Usage:
 *   let state = initialViewState();      // { idx: 1, dir: 1 } — starts on 'grid'
 *   nextViewMode(state);                 // mode the button announces next
 *   state = advanceViewState(state);     // call on button click
 *   currentViewMode(state);
 *   state = resetViewState();            // grid:select shortcut jumps to single
 */
/* exported VIEW_MODES, initialViewState, resetViewState, currentViewMode, nextViewMode, advanceViewState */

const VIEW_MODES = Object.freeze(['single', 'grid', 'stack']);

/** @typedef {{ idx: number, dir: 1|-1 }} ViewState */

/** @returns {ViewState} */
function initialViewState() {
    return { idx: 1, dir: 1 };
}

/** @returns {ViewState} */
function resetViewState() {
    return { idx: 0, dir: 1 };
}

/** @param {ViewState} state @returns {string} */
function currentViewMode(state) {
    return VIEW_MODES[state.idx];
}

/** @param {ViewState} state @returns {string} mode the button will navigate to on click */
function nextViewMode(state) {
    return VIEW_MODES[state.idx + state.dir];
}

/**
 * @param {ViewState} state
 * @returns {ViewState} new state after one bounce step (does not mutate input)
 */
function advanceViewState(state) {
    const idx = state.idx + state.dir;
    const atBoundary = idx === 0 || idx === VIEW_MODES.length - 1;
    return { idx, dir: atBoundary ? /** @type {1|-1} */ (-state.dir) : state.dir };
}
