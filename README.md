# MyApps

Personal HTML apps, hosted via GitHub Pages at:
**https://willbyron88.github.io/MyApps/**

## Structure
```
MyApps/
├── index.html        # landing page (menu of all apps)
├── style.css         # shared styles for landing page
├── willhealthdashboard/
│   └── index.html    # supplements & diet tracker
└── ...
```

## Adding a new app
1. Create a new folder, e.g. `MyApps/macro-tracker/`
2. Add an `index.html` inside it.
3. In the root `index.html`, copy an existing `<a class="card">` block and update the `href`, icon, title, and description.
4. Commit and push — GitHub Pages auto-deploys in ~30–60 seconds.
