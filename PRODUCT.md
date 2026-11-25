# AutoCutPR

A panel that lives inside Adobe Premiere and turns a podcast episode into finished
vertical reels: it finds the moments, cuts them, frames them, brands them, scores them, and
exports them.

**register: product**

## Who uses it

One video editor, and a small team around them, cutting the Romanian interview podcast
*Ce ai în plan*. Every episode is roughly an hour of two people talking, and every episode
owes about ten vertical reels plus a trailer.

They are not the client. A client sends a list of rough timecodes and half-remembered
quotes; the editor is the person who has to turn that into frames. They know Premiere
deeply and do not need an editing tool. They need the forty minutes of hunting, trimming,
resizing and branding to stop being their problem.

## What it is for

Two jobs, and they are different.

1. **Execute a list.** A client list arrives, mistyped and approximate. Turn it into cut,
   framed, branded, exported reels without the editor opening a spreadsheet.
2. **Find the list.** No client list. Read the episode and propose the moments worth
   cutting, with a reason attached, so the editor is reviewing suggestions rather than
   scrubbing an hour of timeline.

The second job is why the tool has to explain itself. A suggestion nobody trusts is worse
than no suggestion.

## Tone

Plain and specific. Say what happened and what it will do. Numbers where numbers help:
"host speaks 4s", not "mostly guest". Never enthusiastic about its own output.

When it is unsure, it says so and shows the evidence. Every automatic decision is visible
before it is committed, because the editor's judgement is the last word and the interface
should act like it knows that.

## Anti-references

- **The AI tool that hides its reasoning.** A score with no explanation is noise. Anything
  the model decides comes with the quote it decided from.
- **The wall of controls.** The previous version of this panel: every option ever added,
  all visible at once, no order. Growth by accretion.
- **The dashboard aesthetic.** Big numbers, sparklines, gradient tiles. This is a workbench.
  Nothing here needs a KPI.
- **Enthusiasm as interface.** No "✨ AI-powered". No celebration copy.

## Principles

- **Nothing is committed without review.** Discovery proposes; the editor disposes.
- **Show the evidence next to the claim.** A moment shows its transcript, its speaker split
  and its length, not just a rank.
- **The panel is a guest in Premiere.** It sits inside someone else's dark UI, next to
  footage being graded. It should never be the brightest thing on screen.
- **Failure is informative.** When a match is weak or a filter rejects something, say which
  and why. Silent degradation is the enemy: this tool has already shipped bugs that were
  invisible until someone watched the output.
