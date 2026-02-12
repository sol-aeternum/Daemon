# PWA Setup & Icon Generation

This project is configured as a PWA. The manifest and metadata are set up.

## Icon Generation

The source icon is located at `public/icons/icon.svg`.
You need to generate the PNG icons for the PWA to work correctly on all devices.

### Using `sharp` (Recommended)

1.  Install `sharp` (if not already installed, or use `npx`):
    ```bash
    npm install sharp
    ```

2.  Create a script `generate-icons.js`:
    ```javascript
    const sharp = require('sharp');
    const fs = require('fs');
    const path = require('path');

    const sizes = [72, 96, 128, 144, 152, 192, 384, 512];
    const input = path.join(__dirname, 'public/icons/icon.svg');
    const outputDir = path.join(__dirname, 'public/icons');

    sizes.forEach(size => {
      sharp(input)
        .resize(size, size)
        .toFile(path.join(outputDir, `icon-${size}x${size}.png`))
        .then(() => console.log(`Generated ${size}x${size}`))
        .catch(err => console.error(err));
    });
    ```

3.  Run the script:
    ```bash
    node generate-icons.js
    ```

### Using ImageMagick

If you have ImageMagick installed:

```bash
magick public/icons/icon.svg -resize 72x72 public/icons/icon-72x72.png
magick public/icons/icon.svg -resize 96x96 public/icons/icon-96x96.png
magick public/icons/icon.svg -resize 128x128 public/icons/icon-128x128.png
magick public/icons/icon.svg -resize 144x144 public/icons/icon-144x144.png
magick public/icons/icon.svg -resize 152x152 public/icons/icon-152x152.png
magick public/icons/icon.svg -resize 192x192 public/icons/icon-192x192.png
magick public/icons/icon.svg -resize 384x384 public/icons/icon-384x384.png
magick public/icons/icon.svg -resize 512x512 public/icons/icon-512x512.png
```
