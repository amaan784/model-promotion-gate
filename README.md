# model-promotion-gate
Every model gets a merge request. A GitLab Duo flow that trains, scores and scans each candidate model, writes the model card, and opens a GO / NO-GO merge request. Merging promotes it to production, with a Release, an Environments deployment and an SCI carbon figure.
GO means every rule passes. NEEDS_REVIEW means only `slice_gap` or `carbon_efficiency`
