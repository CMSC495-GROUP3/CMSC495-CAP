# OpenAI Integration Checklist

## Purpose

This checklist controls the temporary OpenAI connection used by the capstone project. It keeps cost, access, deployment, and retirement decisions visible while keeping the actual API key outside GitHub and project documentation.

## Ownership

| Responsibility | Owner |
| --- | --- |
| OpenAI project and usage monitoring | Chris |
| EC2 environment configuration | George |
| Backend integration support | Daniel and Chris |
| Functional evaluation | Chris |
| Merge approval | Repository maintainer |

## Preparation

1. Create a dedicated OpenAI project for the capstone rather than reusing an unrelated personal or production project.
2. Select the least expensive models that satisfy the application requirements. The repository currently defaults to `gpt-4o`, `gpt-4o-mini`, and `text-embedding-3-small`; confirm those choices before enabling traffic.
3. Set the lowest practical project budget and usage alerts. Confirm whether the platform treats the budget as a notification threshold or an enforced ceiling. Do not rely on a budget display as the only protection against spending.
4. Keep the application password, rate limiting, and restricted EC2 network exposure enabled before adding the key.
5. Create a project scoped key for this application only. Do not reuse a key that grants access to unrelated work.

## Secret handling

1. Place `OPENAI_API_KEY` only in the untracked `.env` file on EC2.
2. Keep `LLM_PROVIDER=openai` in the EC2 `.env` file.
3. Leave `OPENAI_API_KEY=` blank in `.env.example`; that committed file contains field names and placeholders only.
4. Never paste the key into Discord, GitHub issues, pull requests, wiki pages, screenshots, test reports, or source files.
5. Team members may send George public SSH keys. Private SSH keys must remain on the machine where they were generated.
6. Verify that `.env` is ignored by Git before any commit or pull request is created.

## Controlled activation

1. Confirm the EC2 deployment, MongoDB connection, policy corpus, login protection, and HTTPS address are functioning before enabling OpenAI traffic.
2. Add the key through a private EC2 terminal session.
3. Restart only the required application service so it reads the updated environment.
4. Submit one known supported policy question and verify an answer, citation, confidence value, and usage record.
5. Submit one unsupported question and verify refusal without an unnecessary generation call.
6. Run the approved evaluation set in a controlled batch while watching project usage.
7. Stop testing immediately if spending, error rate, or behavior differs materially from the expected result.

## Evidence without disclosure

Record the following without capturing the key:

1. OpenAI project name.
2. Date and time of activation.
3. Selected models.
4. Budget and alert configuration.
5. Initial and final usage totals.
6. Evaluation result summary.
7. Any errors, defects, or unexpected costs.
8. Date and time the key was rotated or deleted.

## Retirement

1. Delete or revoke the project key when the course project no longer requires live OpenAI access.
2. Remove the key from the EC2 `.env` file.
3. Restart the application with the provider disabled or the deployment stopped.
4. Confirm no unexpected usage appears after retirement.
5. Preserve only the nonsecret test results and configuration record needed for the final presentation.
