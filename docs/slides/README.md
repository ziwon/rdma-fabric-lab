# Presentation drafts

| Talk | Length | Present | Edit or open locally |
| --- | --- | --- | --- |
| Ready Is Not Wired: A Reboot Test for Kubernetes Secondary Networks | 8 slides, 10 minutes | [Browser slides](https://rdma.restack.tech/slides/multus-boot-race/) | [Standalone HTML](multus-boot-race.html) |
| Rehearsing a GPU Region on One Host Before the Racks Arrive | 14 slides, 25 minutes | [Browser slides](https://rdma.restack.tech/slides/gpu-region-rehearsal/) | [Standalone HTML](gpu-region-rehearsal.html) |

Both are drafts for the proposals in [proposals.md](proposals.md). Neither talk has been submitted or accepted.

Open the HTML in a browser to use a deck offline. Use the previous/next buttons or the arrow keys to advance, and press **F** for fullscreen where supported. A URL fragment such as `#slide-5` opens that slide directly. Expand the speaker notes beneath each slide for timing and sources, or print to PDF for one slide per page.

The demo slide of the first deck shows the verdicts and details of two lab runs. It is not a terminal capture. `.site/tests/slides.test.mjs` compares the decks with this table, the scenario spec, the break script and the stored excerpts of those runs, so `npm test` in `.site` fails when a check name, a run, a boot time or a source line on a deck no longer matches the repository.

The decks use the [Restack design system](https://github.com/restack-ai/design-system) palette and typography.
