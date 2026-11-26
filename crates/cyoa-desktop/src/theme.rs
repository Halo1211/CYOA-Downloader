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
    pub log_fg: Color32,
    pub sel_row: Color32,
    pub sel_bar: Color32,
    pub sel_nm: Color32,
    pub sel_desc: Color32,
    pub danger_fg: Color32,
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
    log_fg: hex(0x475569),
    sel_row: hex(0x0f1729),
    sel_bar: hex(0x3b82f6),
    sel_nm: hex(0x60a5fa),
    sel_desc: hex(0x3b82f6),
    danger_fg: hex(0xf87171),
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
    log_fg: hex(0x475569),
    sel_row: hex(0xdbeafe),
    sel_bar: hex(0x3b82f6),
    sel_nm: hex(0x1d4ed8),
    sel_desc: hex(0x2563eb),
    danger_fg: hex(0xdc2626),
    srv_fg: hex(0x059669),
    retry_asset: (hex(0xfef3c7), hex(0xb45309)),
    retry_image: (hex(0xede9fe), hex(0x6d28d9)),
    retry_audio: (hex(0xcffafe), hex(0x0e7490)),
    settings: (hex(0xdcfce7), hex(0x166534)),
    manager: (hex(0xf3e8ff), hex(0x7e22ce)),
};

/// `theme_accent_color` (default `#3b82f6`), applied over both palettes.
static ACCENT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0x3b82f6);

/// Accent presets offered in the header (Python default first).
pub const ACCENT_PRESETS: [&str; 6] = [
    "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#ec4899",
];

/// Parse `#rrggbb`; anything else is rejected.
pub fn parse_accent(value: &str) -> Option<u32> {
    let v = value.trim().strip_prefix('#')?;
    (v.len() == 6)
        .then(|| u32::from_str_radix(v, 16).ok())
        .flatten()
}

fn accented(mut p: Palette) -> Palette {
    let a = hex(ACCENT.load(std::sync::atomic::Ordering::Relaxed));
    p.accent = a;
    p.sel_bar = a;
    p.sel_desc = a;
    p
}

// ── Advanced theme editor ─────────────────────────────────────────────

/// Palette colors the theme editor can override: (key, label).
pub const EDITABLE: [(&str, &str); 9] = [
    ("bg", "Background"),
    ("panel", "Panel"),
    ("sidebar", "Sidebar"),
    ("surface", "Surface"),
    ("surface2", "Raised surface"),
    ("input_bg", "Input fields"),
    ("fg", "Text"),
    ("muted", "Muted text"),
    ("border", "Border"),
];

/// UI zoom limits for the editor's scale slider.
pub const SCALE_RANGE: std::ops::RangeInclusive<f32> = 0.8..=1.5;

#[derive(Clone, Default)]
struct Overrides {
    dark: std::collections::BTreeMap<&'static str, u32>,
    light: std::collections::BTreeMap<&'static str, u32>,
    scale: Option<f32>,
}

static OVERRIDES: std::sync::Mutex<Option<Overrides>> = std::sync::Mutex::new(None);

fn with_overrides<T>(f: impl FnOnce(&mut Overrides) -> T) -> T {
    let mut guard = OVERRIDES
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    f(guard.get_or_insert_with(Overrides::default))
}

fn field<'a>(p: &'a mut Palette, key: &str) -> Option<&'a mut Color32> {
    Some(match key {
        "bg" => &mut p.bg,
        "panel" => &mut p.panel,
        "sidebar" => &mut p.sidebar,
        "surface" => &mut p.surface,
        "surface2" => &mut p.surface2,
        "input_bg" => &mut p.input_bg,
        "fg" => &mut p.fg,
        "muted" => &mut p.muted,
        "border" => &mut p.border,
        _ => return None,
    })
}

/// Base palette + accent + the user's overrides.
fn themed(dark: bool) -> Palette {
    let mut p = accented(if dark { DARK } else { LIGHT });
    let map = with_overrides(|o| {
        if dark {
            o.dark.clone()
        } else {
            o.light.clone()
        }
    });
    for (key, rgb) in map {
        if let Some(slot) = field(&mut p, key) {
            *slot = hex(rgb);
        }
    }
    p
}

pub fn palette(ui: &egui::Ui) -> Palette {
    themed(ui.visuals().dark_mode)
}

/// The effective color of an editable key, and whether it is overridden.
pub fn editable_color(dark: bool, key: &str) -> (Color32, bool) {
    let mut p = themed(dark);
    let overridden = with_overrides(|o| {
        if dark {
            o.dark.contains_key(key)
        } else {
            o.light.contains_key(key)
        }
    });
    (
        field(&mut p, key).map_or(Color32::TRANSPARENT, |c| *c),
        overridden,
    )
}

/// Set (`Some`) or clear (`None`) one palette override.
pub fn set_override(dark: bool, key: &str, color: Option<Color32>) {
    let Some(&(key, _)) = EDITABLE.iter().find(|(k, _)| *k == key) else {
        return;
    };
    with_overrides(|o| {
        let map = if dark { &mut o.dark } else { &mut o.light };
        match color {
            Some(c) => {
                map.insert(
                    key,
                    (u32::from(c.r()) << 16) | (u32::from(c.g()) << 8) | u32::from(c.b()),
                );
            }
            None => {
                map.remove(key);
            }
        }
    });
}

pub fn ui_scale() -> f32 {
    with_overrides(|o| o.scale).unwrap_or(1.0)
}

pub fn set_ui_scale(scale: f32) {
    let s = scale.clamp(*SCALE_RANGE.start(), *SCALE_RANGE.end());
    with_overrides(|o| o.scale = ((s - 1.0).abs() > f32::EPSILON).then_some(s));
}

/// Drop every override and restore the default accent.
pub fn reset_overrides() {
    with_overrides(|o| *o = Overrides::default());
    ACCENT.store(0x3b82f6, std::sync::atomic::Ordering::Relaxed);
}

pub fn accent_hex() -> String {
    format!("#{:06x}", ACCENT.load(std::sync::atomic::Ordering::Relaxed))
}

/// Settings `theme_custom` (`{"dark": {"bg": "#rrggbb"}, "light": {...}}`)
/// and `ui_scale`.
pub fn load_overrides(settings: &serde_json::Map<String, serde_json::Value>) {
    let mut o = Overrides::default();
    if let Some(custom) = settings.get("theme_custom").and_then(|v| v.as_object()) {
        for (mode, target) in [("dark", &mut o.dark), ("light", &mut o.light)] {
            let Some(map) = custom.get(mode).and_then(|v| v.as_object()) else {
                continue;
            };
            for (key, _) in EDITABLE {
                if let Some(rgb) = map.get(key).and_then(|v| v.as_str()).and_then(parse_accent) {
                    target.insert(key, rgb);
                }
            }
        }
    }
    o.scale = settings
        .get("ui_scale")
        .and_then(serde_json::Value::as_f64)
        .map(|s| (s as f32).clamp(*SCALE_RANGE.start(), *SCALE_RANGE.end()))
        .filter(|s| (s - 1.0).abs() > f32::EPSILON);
    with_overrides(|cur| *cur = o);
}

/// `theme_custom` value for settings.json.
pub fn overrides_json() -> serde_json::Value {
    with_overrides(|o| {
        let side = |m: &std::collections::BTreeMap<&'static str, u32>| {
            serde_json::Value::Object(
                m.iter()
                    .map(|(k, v)| {
                        (
                            (*k).to_owned(),
                            serde_json::Value::String(format!("#{v:06x}")),
                        )
                    })
                    .collect(),
            )
        };
        serde_json::json!({ "dark": side(&o.dark), "light": side(&o.light) })
    })
}

/// Re-install both visuals and the zoom factor after any theme change.
pub fn reapply(ctx: &egui::Context) {
    ctx.set_visuals_of(egui::Theme::Dark, visuals(&themed(true), true));
    ctx.set_visuals_of(egui::Theme::Light, visuals(&themed(false), false));
    ctx.set_zoom_factor(ui_scale());
}

/// Switch the accent at runtime (invalid values keep the current one).
pub fn apply_accent(ctx: &egui::Context, value: &str) -> bool {
    let Some(rgb) = parse_accent(value) else {
        return false;
    };
    ACCENT.store(rgb, std::sync::atomic::Ordering::Relaxed);
    reapply(ctx);
    true
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
    v.text_edit_bg_color = Some(p.input_bg);
    v.faint_bg_color = p.surface;
    v.weak_text_color = Some(p.muted);
    v.hyperlink_color = p.accent;
    v.selection.bg_fill = p.accent;
    v.selection.stroke = Stroke::new(1.0, Color32::WHITE);
    v.window_corner_radius = CornerRadius::same(12);
    v.window_stroke = Stroke::new(1.0, p.border);
    let r = CornerRadius::same(6);
    let hover = if dark { hex(0x263046) } else { hex(0xb6c2d2) };
    for (w, fill, text) in [
        (&mut v.widgets.noninteractive, p.panel, p.fg),
        (&mut v.widgets.inactive, p.surface2, p.fg),
        (&mut v.widgets.hovered, hover, p.fg),
        (&mut v.widgets.active, p.accent, Color32::WHITE),
        (&mut v.widgets.open, p.surface2, p.fg),
    ] {
        w.corner_radius = r;
        w.bg_fill = fill;
        w.weak_bg_fill = fill;
        w.fg_stroke = Stroke::new(1.0, text);
        w.expansion = 0.0;
    }
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, p.separator);
    v.widgets.inactive.bg_stroke = Stroke::new(1.0, p.border);
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, p.muted2);
    v.widgets.active.bg_stroke = Stroke::new(1.0, p.accent);
    v
}

/// Install both palettes, Segoe UI (Windows) and the saved theme mode.
pub fn install(ctx: &egui::Context, theme_mode: &str, accent: &str) {
    if let Some(rgb) = parse_accent(accent) {
        ACCENT.store(rgb, std::sync::atomic::Ordering::Relaxed);
    }
    reapply(ctx);
    apply_theme_mode(ctx, theme_mode);
    let mut fonts = egui::FontDefinitions::default();
    let windir = std::env::var("WINDIR").unwrap_or_else(|_| r"C:\Windows".into());
    // Primary faces first; Segoe UI Symbol as the icon fallback right after
    // them (Tk renders the sidebar and button icons through Segoe symbols).
    for (name, file, family, first) in [
        (
            "segoe-ui",
            "segoeui.ttf",
            egui::FontFamily::Proportional,
            true,
        ),
        ("consolas", "consola.ttf", egui::FontFamily::Monospace, true),
        // Tk draws icons with Segoe UI Emoji's monochrome (outline) glyphs.
        (
            "segoe-emoji",
            "seguiemj.ttf",
            egui::FontFamily::Proportional,
            false,
        ),
        (
            "segoe-symbol",
            "seguisym.ttf",
            egui::FontFamily::Proportional,
            false,
        ),
    ] {
        let path = std::path::Path::new(&windir).join("Fonts").join(file);
        if let Ok(bytes) = std::fs::read(&path) {
            fonts.font_data.insert(
                name.into(),
                std::sync::Arc::new(egui::FontData::from_owned(bytes)),
            );
            let fam = fonts.families.entry(family).or_default();
            if first {
                fam.insert(0, name.into());
            } else {
                // After Segoe UI and any earlier symbol fallback.
                let at = fam
                    .iter()
                    .position(|f| f == "segoe-emoji")
                    .map_or(1, |i| i + 1)
                    .min(fam.len());
                fam.insert(at, name.into());
            }
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
    ctx.all_styles_mut(|style| {
        use egui::{FontFamily, FontId, TextStyle};
        style.text_styles = [
            (
                TextStyle::Heading,
                FontId::new(14.0, FontFamily::Proportional),
            ),
            (TextStyle::Body, FontId::new(10.5, FontFamily::Proportional)),
            (
                TextStyle::Button,
                FontId::new(10.5, FontFamily::Proportional),
            ),
            (TextStyle::Small, FontId::new(8.5, FontFamily::Proportional)),
            (
                TextStyle::Monospace,
                FontId::new(10.0, FontFamily::Monospace),
            ),
        ]
        .into();
    });
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

/// Sidebar descriptions as the Python GUI renders them (English).
pub fn sidebar_desc(e: &ModeEntry, auto_pref: &str) -> &'static str {
    match e.key {
        // Python `_auto_sidebar_default_desc`.
        "auto" => match (crate::i18n::is_indonesian(), auto_pref == "zip") {
            (false, true) => "Default: ZIP output",
            (false, false) => "Default: Folder output",
            (true, true) => "Default: output ZIP",
            (true, false) => "Default: output Folder",
        },
        "website_zip" => "Offline viewer archive",
        "website_folder" => "Offline viewer folder",
        "pure_website_folder" => "Viewer folder, no project scan",
        "cyoap_vue_zip" => "cyoap_vue engine archive",
        "cyoap_vue_folder" => "cyoap_vue engine folder",
        _ => e.desc,
    }
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
        mode: Some(Mode::Auto),
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
        Mode::Auto => (hex(0x1e3a8a), hex(0x93c5fd)),
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
        .size(9.0);
    ui.add_enabled(
        enabled,
        egui::Button::new(text)
            .fill(colors.0)
            .corner_radius(CornerRadius::same(7))
            .min_size(egui::vec2(0.0, 26.0)),
    )
}

/// Python CTkCheckBox: rounded box, filled with `color` when checked.
pub fn checkbox(
    ui: &mut egui::Ui,
    p: &Palette,
    on: &mut bool,
    label: &str,
    color: Color32,
    enabled: bool,
) -> egui::Response {
    let font = egui::FontId::proportional(11.0);
    let galley =
        ui.painter()
            .layout_no_wrap(label.to_owned(), font, if enabled { p.fg } else { p.muted });
    let size = egui::vec2(18.0 + 6.0 + galley.size().x, 20.0);
    let (rect, mut resp) = ui.allocate_exact_size(
        size,
        if enabled {
            egui::Sense::click()
        } else {
            egui::Sense::hover()
        },
    );
    if enabled && resp.clicked() {
        *on = !*on;
        resp.mark_changed();
    }
    let boxr = egui::Rect::from_center_size(
        egui::pos2(rect.min.x + 11.0, rect.center().y),
        egui::vec2(20.0, 20.0),
    );
    let painter = ui.painter();
    if *on && enabled {
        painter.rect_filled(boxr, CornerRadius::same(5), color);
        let pts = [
            boxr.min + egui::vec2(4.0, 8.5),
            boxr.min + egui::vec2(7.0, 11.5),
            boxr.min + egui::vec2(12.5, 5.0),
        ];
        painter.line_segment([pts[0], pts[1]], Stroke::new(1.8, Color32::WHITE));
        painter.line_segment([pts[1], pts[2]], Stroke::new(1.8, Color32::WHITE));
    } else {
        painter.rect_stroke(
            boxr,
            CornerRadius::same(5),
            Stroke::new(2.5, p.muted2),
            egui::StrokeKind::Inside,
        );
    }
    painter.galley(
        egui::pos2(rect.min.x + 24.0, rect.center().y - galley.size().y / 2.0),
        galley,
        p.fg,
    );
    resp
}

/// Flat filled button (Python CTkButton with `fg_color`).
pub fn flat(
    ui: &mut egui::Ui,
    label: impl Into<egui::WidgetText>,
    fill: Color32,
    size: egui::Vec2,
    enabled: bool,
) -> egui::Response {
    ui.add_enabled(
        enabled,
        egui::Button::new(label)
            .fill(fill)
            .stroke(Stroke::NONE)
            .corner_radius(CornerRadius::same(6))
            .min_size(size),
    )
}

/// CTkOptionMenu arrow: a chevron on a darker button segment at the right.
pub fn combo_icon(ui: &egui::Ui, rect: egui::Rect, _v: &egui::style::WidgetVisuals, open: bool) {
    let seg = egui::Rect::from_center_size(
        rect.center(),
        egui::vec2(rect.width() + 12.0, rect.height() + 6.0),
    );
    let fill = if ui.visuals().dark_mode {
        Color32::from_rgb(0x14, 0x37, 0x5e)
    } else {
        Color32::from_rgb(0x36, 0x71, 0x9f)
    };
    ui.painter().rect_filled(seg, CornerRadius::same(5), fill);
    let c = rect.center();
    let (dy, w) = (2.5, 4.0);
    let pts = if open {
        [
            c + egui::vec2(-w, dy),
            c + egui::vec2(0.0, -dy),
            c + egui::vec2(w, dy),
        ]
    } else {
        [
            c + egui::vec2(-w, -dy),
            c + egui::vec2(0.0, dy),
            c + egui::vec2(w, -dy),
        ]
    };
    let st = Stroke::new(1.6, Color32::WHITE);
    ui.painter().line_segment([pts[0], pts[1]], st);
    ui.painter().line_segment([pts[1], pts[2]], st);
}

#[cfg(test)]
mod accent_tests {
    use super::*;

    #[test]
    fn accent_parsing_and_presets() {
        assert_eq!(parse_accent("#3b82f6"), Some(0x3b82f6));
        assert_eq!(parse_accent(" #EF4444 "), Some(0xef4444));
        for bad in ["", "3b82f6", "#3b82f", "#zzzzzz", "#3b82f6ff"] {
            assert_eq!(parse_accent(bad), None, "{bad}");
        }
        assert!(ACCENT_PRESETS.iter().all(|p| parse_accent(p).is_some()));
    }

    #[test]
    fn overrides_round_trip_and_reset() {
        let settings: serde_json::Map<String, serde_json::Value> = serde_json::from_str(
            r##"{"theme_custom":{"dark":{"bg":"#101010","bogus":"#ffffff"},"light":{"fg":"#000001"}},"ui_scale":1.25}"##,
        )
        .unwrap_or_default();
        load_overrides(&settings);
        assert_eq!(editable_color(true, "bg"), (hex(0x101010), true));
        assert_eq!(editable_color(false, "fg"), (hex(0x000001), true));
        assert!((ui_scale() - 1.25).abs() < 1e-6);
        let saved = overrides_json();
        assert_eq!(saved["dark"]["bg"], "#101010");
        assert!(saved["dark"].get("bogus").is_none());
        reset_overrides();
        assert_eq!(editable_color(true, "bg"), (DARK.bg, false));
        assert!((ui_scale() - 1.0).abs() < 1e-6);
    }
}
