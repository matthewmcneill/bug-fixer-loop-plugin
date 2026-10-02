with open("scripts/architect_dispatcher.py", "r") as f:
    c = f.read()

# Fix 1
c = c.replace(
    'r = gh("pr", "list", "--repo", TARGET_REPO, "--label", "bug-fixer:system:pr",',
    'r = gh("pr", "list", "--repo", TARGET_REPO, "--label", "bug-fixer:system:pr,bug-fixer:architect:awaiting-review",'
)

# Fix 2
c = c.replace(
    'gh("pr", "edit", str(number), "--repo", TARGET_REPO,\n               "--add-label", "bug-fixer:human:needs-intervention")',
    'gh("pr", "edit", str(number), "--repo", TARGET_REPO,\n               "--add-label", "bug-fixer:human:needs-intervention",\n               "--remove-label", "bug-fixer:architect:awaiting-review")'
)

with open("scripts/architect_dispatcher.py", "w") as f:
    f.write(c)
