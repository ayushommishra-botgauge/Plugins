# Review rules

`/botgauge:review` reads this file before reviewing (so does Anthropic's Code Review, if this repo ever turns it on). Keep it short.
- When the review keeps flagging something that isn't a problem here, add a line under "Don't report".
- When it misses something, add a line under "Always check".

## What failure looks like here

<!-- One line for each thing that matters most in this repo. Examples: -->
- <e.g. An exception escaping from our library into the customer's app is a merge blocker.>
- <e.g. Secrets or personal data leaving the process unmasked is a merge blocker.>
- For docs: a docstring or doc that says something the code doesn't do is high priority.

## Sensitive paths (always reviewed in depth)

- <path/one/>
- <path/two/>

## Docstring standard

- Style: <Google | NumPy | TSDoc | JSDoc | Go doc comments>.
- Public functions, classes and modules have a docstring. New or changed ones describe their parameters, return value and the errors callers can see.
- Match the surrounding code's comment density. Comments explain why, not what.

## Severity overrides

<!-- Optional. For example: "Missing pagination on list endpoints is high here, not medium." -->

## Always check

<!-- Repo-specific things reviewers should look for. -->

## Don't report

- Anything CI already enforces: lint, formatting, type errors.
- Generated or vendored files: <paths>.

## Evidence

- Every finding names a file and line and shows why. No guesses from a name.
- At most 5 low findings per review.
