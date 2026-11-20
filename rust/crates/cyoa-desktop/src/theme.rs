//! Python GUI look: the `_p()` palette (dark and light), Segoe UI, rounded
//! cards, mode metadata and badge colors (`CYOADownloaderGUI.MODES`,
//! `BADGE_COLORS`).

use crate::app::Mode;
use eframe::egui::{self, Color32, CornerRadius, Stroke};

const fn hex(v: u32) -> Color32 {
    Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

/// Python `_p()` palette keys used by the Rust layout.
#[derive(Clone, Copy)]
pub struct Palette {
    pub bg: Color32,
    pub panel: Color32,
    pub surface: Color32,
    pub surface2: Color32,
    pub fg: Color32,
    pub muted: Color32,
    pub muted2: Color32,
    pub accent: Color32,
    pub border: Color32,
    pub separator: Color32,
    pub sidebar: Color32,
    pub input_bg: Color32,
    pub log_bg: Color32,
    pub log_fg: Color32,
    pub sel_row: Color32,
    pub sel_bar: Color32,
    pub sel_nm: Color32,
    pub sel_desc: Color32,
    pub danger_fg: Color32,
    pub accentbg: Color32,
    pub srv_fg: Color32,
    pub retry_asset: (Color32, Color32),
    pub retry_image: (Color32, Color32),
    pub retry_audio: (Color32, Color32),
    pub settings: (Color32, Color32),
    pub manager: (Color32, Color32),
}

pub const DARK: Palette = Palette {
    bg: hex(0x0e1117),
    panel: hex(0x0a0d13),
    surface: hex(0x141922),
    surface2: hex(0x1e2433),
    fg: hex(0xe2e8f0),
    muted: hex(0x475569),
    muted2: hex(0x334155),
    accent: hex(0x3b82f6),
    border: hex(0x1e2433),
    separator: hex(0x202a3a),
    sidebar: hex(0x0a0d13),
    input_bg: hex(0x141922),
    log_bg: hex(0x0a0d13),
    log_fg: hex(0x475569),
    sel_row: hex(0x0f1729),
    sel_bar: hex(0x3b82f6),
    sel_nm: hex(0x60a5fa),
    sel_desc: hex(0x3b82f6),
    danger_fg: hex(0xf87171),
    accentbg: hex(0x0c1a2e),
    srv_fg: hex(0x6ee7b7),
    retry_asset: (hex(0x3b2506), hex(0xfbbf24)),
    retry_image: (hex(0x2e1065), hex(0xc4b5fd)),
    retry_audio: (hex(0x083344), hex(0x67e8f9)),
    settings: (hex(0x052e16), hex(0x86efac)),
    manager: (hex(0x2e1065), hex(0xd8b4fe)),
};

pub const LIGHT: Palette = Palette {
    bg: hex(0xf1f5f9),
    panel: hex(0xffffff),
    surface: hex(0xe2e8f0),
    surface2: hex(0xcbd5e1),
    fg: hex(0x0f172a),
    muted: hex(0x64748b),
    muted2: hex(0x94a3b8),
    accent: hex(0x3b82f6),
    border: hex(0xe2e8f0),
    separator: hex(0xd7dee8),
    sidebar: hex(0xf8fafc),
    input_bg: hex(0xffffff),
    log_bg: hex(0xf8fafc),
    log_fg: hex(0x475569),
    sel_row: hex(0xdbeafe),
    sel_bar: hex(0x3b82f6),
    sel_nm: hex(0x1d4ed8),
    sel_desc: hex(0x2563eb),
    danger_fg: hex(0xdc2626),
    accentbg: hex(0xdbeafe),
    srv_fg: hex(0x059669),
    retry_asset: (hex(0xfef3c7), hex(0xb45309)),
    retry_image: (hex(0xede9fe), hex(0x6d28d9)),
    retry_audio: (hex(0xcffafe), hex(0x0e7490)),
    settings: (hex(0xdcfce7), hex(0x166534)),
    manager: (hex(0xf3e8ff), hex(0x7e22ce)),
};

pub fn palette(ui: &egui::Ui) -> Palette {
    if ui.visuals().dark_mode { DARK } else { LIGHT }
}

fn visuals(p: &Palette, dark: bool) -> egui::Visuals {
    let mut v = if dark {
        egui::Visuals::dark()
    } else {
        egui::Visuals::light()
    };
    v.panel_fill = p.bg;
    v.window_fill = p.panel;
    v.extreme_bg_color = p.input_bg;
    v.faint_bg_color = p.surface;
    v.override_text_color = Some(p.fg);
    v.hyperlink_color = p.accent;
    v.selection.bg_fill = p.accent;
    v.selection.stroke = Stroke::new(1.0, Color32::WHITE);
    v.window_corner_radius = CornerRadius::same(12);
    v.window_stroke = Stroke::new(1.0, p.border);
    let r = CornerRadius::same(7);
    for (w, fill) in [
        (&mut v.widgets.noninteractive, p.panel),
        (&mut v.widgets.inactive, p.surface2),
        (&mut v.widgets.hovered, p.muted2),
        (&mut v.widgets.active, p.accent),
        (&mut v.widgets.open, p.surface2),
    ] {
        w.corner_radius = r;
        w.bg_fill = fill;
        w.weak_bg_fill = fill;
    }
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, p.separator);
    v.widgets.inactive.bg_stroke = Stroke::NONE;
    v
}

/// Install both palettes, Segoe UI (Windows) and the saved theme mode.
pub fn install(ctx: &egui::Context, theme_mode: &str) {
    ctx.set_visuals_of(egui::Theme::Dark, visuals(&DARK, true));
    ctx.set_visuals_of(egui::Theme::Light, visuals(&LIGHT, false));
    apply_theme_mode(ctx, theme_mode);
    let mut fonts = egui::FontDefinitions::default();
    let windir = std::env::var("WINDIR").unwrap_or_else(|_| r"C:\Windows".into());
    for (name, file, family) in [
        ("segoe-ui", "segoeui.ttf", egui::FontFamily::Proportional),
        ("consolas", "consola.ttf", egui::FontFamily::Monospace),
    ] {
        let path = std::path::Path::new(&windir).join("Fonts").join(file);
        if let Ok(bytes) = std::fs::read(&path) {
            fonts.font_data.insert(
                name.into(),
                std::sync::Arc::new(egui::FontData::from_owned(bytes)),
            );
            fonts
                .families
                .entry(family)
                .or_default()
                .insert(0, name.into());
        }
    }
    let bold = std::path::Path::new(&windir)
        .join("Fonts")
        .join("segoeuib.ttf");
    if let Ok(bytes) = std::fs::read(&bold) {
        fonts.font_data.insert(
            "segoe-ui-bold".into(),
            std::sync::Arc::new(egui::FontData::from_owned(bytes)),
        );
        let mut fam = vec!["segoe-ui-bold".to_owned()];
        fam.extend(
            fonts
                .families
                .get(&egui::FontFamily::Proportional)
                .cloned()
                .unwrap_or_default(),
        );
        fonts
            .families
            .insert(egui::FontFamily::Name("bold".into()), fam);
    } else {
        let prop = fonts
            .families
            .get(&egui::FontFamily::Proportional)
            .cloned()
            .unwrap_or_default();
        fonts
            .families
            .insert(egui::FontFamily::Name("bold".into()), prop);
    }
    ctx.set_fonts(fonts);
}

/// Python `theme_mode` ("System" / "Light" / "Dark").
pub fn apply_theme_mode(ctx: &egui::Context, mode: &str) {
    ctx.set_theme(match mode {
        "Light" => egui::ThemePreference::Light,
        "Dark" => egui::ThemePreference::Dark,
        _ => egui::ThemePreference::System,
    });
}

/// Bold text in Segoe UI Bold (falls back to the default face).
pub fn bold(text: impl Into<String>, size: f32) -> egui::RichText {
    egui::RichText::new(text)
        .family(egui::FontFamily::Name("bold".into()))
        .size(size)
}

/// Sidebar entries: `None` mode = section header; `Some(None)` = Auto,
/// which the Rust port does not implement yet.
pub struct ModeEntry {
    pub mode: Option<Mode>,
    pub key: &'static str,
    pub icon: &'static str,
    pub name: &'static str,
    pub desc: &'static str,
}

pub const MODES: &[ModeEntry] = &[
    ModeEntry {
        mode: None,
        key: "__sec__",
        icon: "",
        name: "OUTPUT MODE",
        desc: "",
    },
    ModeEntry {
        mode: None,
        key: "auto",
        icon: "⚡",
        name: "Auto (detect)",
        desc: "Default: Settings",
    },
    ModeEntry {
        mode: Some(Mode::Embed),
        key: "embed",
        icon: "📄",
        name: "Embedded JSON",
        desc: "Images embedded in JSON",
    },
    ModeEntry {
        mode: Some(Mode::Zip),
        key: "zip",
        icon: "🗜",
        name: "ZIP",
        desc: "JSON + separate assets",
    },
    ModeEntry {
        mode: Some(Mode::Both),
        key: "both",
        icon: "📦",
        name: "Both",
        desc: "Embed + ZIP together",
    },
    ModeEntry {
        mode: None,
        key: "__sec__",
        icon: "",
        name: "ICC MODE",
        desc: "",
    },
    ModeEntry {
        mode: Some(Mode::IccZip),
        key: "website_zip",
        icon: "🌐",
        name: "ICC ZIP",
        desc: "Offline viewer files",
    },
    ModeEntry {
        mode: Some(Mode::IccFolder),
        key: "website_folder",
        icon: "📁",
        name: "ICC Folder",
        desc: "Offline viewer files",
    },
    ModeEntry {
        mode: None,
        key: "__sec__",
        icon: "",
        name: "PURE WEBSITE",
        desc: "",
    },
    ModeEntry {
        mode: Some(Mode::PureZip),
        key: "pure_website_zip",
        icon: "🔒",
        name: "Pure Website ZIP",
        desc: "Viewer only, no project scan",
    },
    ModeEntry {
        mode: Some(Mode::PureFolder),
        key: "pure_website_folder",
        icon: "🔓",
        name: "Pure Website Folder",
        desc: "Viewer only, no project scan",
    },
    ModeEntry {
        mode: None,
        key: "__sec__",
        icon: "",
        name: "CYOAP_VUE",
        desc: "",
    },
    ModeEntry {
        mode: Some(Mode::CyoapZip),
        key: "cyoap_vue_zip",
        icon: "⚡",
        name: "cyoap_vue ZIP",
        desc: "cyoap_vue engine backup",
    },
    ModeEntry {
        mode: Some(Mode::CyoapFolder),
        key: "cyoap_vue_folder",
        icon: "⚡",
        name: "cyoap_vue Folder",
        desc: "cyoap_vue engine backup",
    },
];

pub fn mode_entry(mode: Mode) -> &'static ModeEntry {
    MODES
        .iter()
        .find(|e| e.mode == Some(mode))
        .unwrap_or(&MODES[2])
}

/// Python `BADGE_COLORS` (background, text).
pub fn badge(mode: Mode) -> (Color32, Color32) {
    match mode {
        Mode::Embed => (hex(0x1e3a5f), hex(0x60a5fa)),
        Mode::Zip => (hex(0x1e1e3b), hex(0xa78bfa)),
        Mode::Both => (hex(0x374151), hex(0xd1d5db)),
        Mode::IccZip | Mode::IccFolder => (hex(0x065f46), hex(0x6ee7b7)),
        Mode::PureZip | Mode::PureFolder => (hex(0x4c1d95), hex(0xc4b5fd)),
        Mode::CyoapZip | Mode::CyoapFolder => (hex(0x78350f), hex(0xfde68a)),
    }
}

/// Colored pill button (Python `_pill`).
pub fn pill(
    ui: &mut egui::Ui,
    icon: &str,
    label: &str,
    colors: (Color32, Color32),
    enabled: bool,
) -> egui::Response {
    let text = egui::RichText::new(format!("{icon} {label}"))
        .color(colors.1)
        .size(12.0);
    ui.add_enabled(
        enabled,
        egui::Button::new(text)
            .fill(colors.0)
            .corner_radius(CornerRadius::same(7))
            .min_size(egui::vec2(0.0, 26.0)),
    )
}

/// Outlined header button (Credits / Diagnostics / Help).
pub fn outline(ui: &mut egui::Ui, p: &Palette, label: &str) -> egui::Response {
    ui.add(
        egui::Button::new(egui::RichText::new(label).color(p.muted).size(12.0))
            .fill(Color32::TRANSPARENT)
            .stroke(Stroke::new(1.0, p.muted2))
            .corner_radius(CornerRadius::same(7))
            .min_size(egui::vec2(0.0, 26.0)),
    )
}

/// Filled primary button.
pub fn primary(ui: &mut egui::Ui, label: &str, fill: Color32, enabled: bool) -> egui::Response {
    ui.add_enabled(
        enabled,
        egui::Button::new(bold(label, 13.0).color(Color32::WHITE))
            .fill(fill)
            .corner_radius(CornerRadius::same(7))
            .min_size(egui::vec2(0.0, 32.0)),
    )
}
