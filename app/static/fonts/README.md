# Local interface fonts

These WOFF2 files are self-hosted so Setuora does not require a connection to Google Fonts at runtime. Only the Latin and Latin Extended subsets are bundled; other scripts use the system fallback font.

- **Inter**: normal variable font, declared weights 400–600. Copyright 2020 The Inter Project Authors. See [Inter-OFL.txt](Inter-OFL.txt).
- **Cormorant Garamond**: normal font, declared weight 500. Copyright 2015 The Cormorant Project Authors. See [Cormorant-Garamond-OFL.txt](Cormorant-Garamond-OFL.txt).
- **JetBrains Mono**: normal font, declared weight 400. Copyright 2020 The JetBrains Mono Project Authors. See [JetBrains-Mono-OFL.txt](JetBrains-Mono-OFL.txt).

These fonts are distributed under the SIL Open Font License 1.1. Font files are unmodified Google Fonts WOFF2 subsets. The accompanying license files were retrieved from the Google Fonts repository through its jsDelivr mirror on 2026-09-18:

- https://github.com/google/fonts/blob/main/ofl/inter/OFL.txt
- https://github.com/google/fonts/blob/main/ofl/cormorantgaramond/OFL.txt
- https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt
- Mirror: `https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/<family>/OFL.txt`

## Font file sources

- `cormorant-garamond-latin-ext.woff2`: https://fonts.gstatic.com/s/cormorantgaramond/v21/co3umX5slCNuHLi8bLeY9MK7whWMhyjypVO7abI26QOD_s06KnrOiss4.woff2
- `cormorant-garamond-latin.woff2`: https://fonts.gstatic.com/s/cormorantgaramond/v21/co3umX5slCNuHLi8bLeY9MK7whWMhyjypVO7abI26QOD_s06KnTOig.woff2
- `inter-latin-ext.woff2`: https://fonts.gstatic.com/s/inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa25L7SUc.woff2
- `inter-latin.woff2`: https://fonts.gstatic.com/s/inter/v20/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7.woff2
- `jetbrains-mono-latin-ext.woff2`: https://fonts.gstatic.com/s/jetbrainsmono/v24/tDbY2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKxTNFOVgaY.woff2
- `jetbrains-mono-latin.woff2`: https://fonts.gstatic.com/s/jetbrainsmono/v24/tDbY2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKxTOlOV.woff2

Unicode ranges and weight declarations come from the Google Fonts CSS returned for these families; local `@font-face` declarations are in `../design-system.css`.
