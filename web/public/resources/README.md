# Home screen image

Put your image in this folder and name it `home.png`:

    web/public/resources/home.png

Use a PNG image (convert it to PNG rather than just renaming another format).
The home screen displays it above the upload form, preserving its proportions.
If no image is present, the image area is hidden.

For another filename or format, change the image `src` in `web/src/main.jsx`.

If you use `npm run dev`, refresh the browser after adding the image.
If Go serves the built frontend, run `npm run build --prefix web` from the
project root and refresh. No Go restart is needed for a static image change.

Files in this folder are public frontend assets. Only add images you want
served to anyone using the app.
