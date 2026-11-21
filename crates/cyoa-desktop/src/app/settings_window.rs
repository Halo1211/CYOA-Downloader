//! Python `_settings_dashboard_panel` window: its own OS window (1080x760,
//! min 820x580) with header, category sidebar, scrolling page and footer.

use super::DownloaderApp;
use crate::settings_dashboard::{PAGES, muted, search_page, title};
use crate::theme;
use eframe::egui::{self, Frame, Margin, RichText};

/// Python page titles and descriptions.
const PAGE_TEXT: &[(&str, &str)] = &[
    (
        "Download",
        "Output, authentication, feature helpers, and archive behavior.",
    ),
    (
        "Network",
        "DNS transport, proxy routing, and a fail-closed VPN guard.",
    ),
    (
        "Integrations",
        "Configure credentials and network helpers directly on this page.",
    ),
    (
        "Offline viewers",
        "Opt-in automation, compatibility recommendations, and local viewer archives.",
    ),
    (
        "Tools & Maintenance",
        "Manage storage and portable settings without another app window.",
    ),
];

pub(crate) const SETTINGS_VIEWPORT: &str = "settings_maintenance";

impl DownloaderApp {
    pub(super) fn settings_viewport(&mut self, ctx: &egui::Context) {
        if !self.show_settings {
            self.dash.closed();
            return;
        }
        let Some(path) = self.shared.as_ref().map(|(p, _)| p.clone()) else {
            self.show_settings = false;
            return;
        };
        self.dash.ensure_loaded(&path);
        if self.ai_panel.is_none() {
            self.ai_panel = Some(crate::ai_panel::AiPanel::open(
                path.clone(),
                &self.ai_session,
            ));
        }
        if self.viewers.is_none() {
            self.viewers = Some(crate::viewers::ViewerPanel::open(path.clone()));
        }
        let id = egui::ViewportId::from_hash_of(SETTINGS_VIEWPORT);
        let builder = egui::ViewportBuilder::default()
            .with_title("Settings / Maintenance")
            .with_inner_size([1080.0, 760.0])
            .with_min_inner_size([820.0, 580.0]);
        let mut close = false;
        let mut new_pref = None;
        ctx.show_viewport_immediate(id, builder, |ui, _class| {
            let vctx = ui.ctx().clone();
            if vctx.input(|i| i.viewport().close_requested()) {
                close = true;
            }
            let p = theme::palette(ui);
            egui::Panel::top("settings_header")
                .show_separator_line(false)
                .exact_size(68.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin {
                    left: 20,
                    right: 20,
                    top: 10,
                    bottom: 8,
                }))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.vertical(|ui| {
                            ui.label(RichText::new("Settings / Maintenance").family(egui::FontFamily::Name("bold".into())).size(18.0).color(p.fg));
                            ui.label(
                                RichText::new(
                                    "Download behavior, integrations, and maintenance in one place.",
                                )
                                .size(10.0)
                                .color(muted(ui)),
                            );
                        });
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            ui.label(RichText::new("CONFIGURATION").family(egui::FontFamily::Name("bold".into())).size(9.0).color(p.accent));
                        });
                    });
                });
            egui::Panel::bottom("settings_footer")
                .show_separator_line(false)
                .exact_size(46.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin::symmetric(16, 8)))
                .show(ui, |ui| {
                    ui.horizontal_centered(|ui| {
                        let status = if self.dash.status.is_empty() {
                            "Changes are saved when you press Save or toggle a switch."
                        } else {
                            self.dash.status.as_str()
                        };
                        ui.label(RichText::new(status).size(9.0).color(muted(ui)));
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui
                                .add(
                                    egui::Button::new(RichText::new("Close").color(p.fg))
                                        .fill(p.surface2)
                                        .min_size(egui::vec2(88.0, 28.0)),
                                )
                                .clicked()
                            {
                                close = true;
                            }
                        });
                    });
                });
            egui::Panel::left("settings_sidebar")
                .show_separator_line(false)
                .exact_size(218.0)
                .resizable(false)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin {
                    left: 10,
                    right: 10,
                    top: 16,
                    bottom: 14,
                }))
                .show(ui, |ui| {
                    ui.label(RichText::new("FIND A SETTING").family(egui::FontFamily::Name("bold".into())).size(9.0).color(p.accent));
                    ui.add_space(4.0);
                    let search = ui.add(
                        egui::TextEdit::singleline(&mut self.dash.search)
                            .hint_text("Search settings")
                            .desired_width(ui.available_width())
                            .margin(Margin::symmetric(8, 7)),
                    );
                    if search.changed() {
                        let query = self.dash.search.trim().to_owned();
                        match search_page(&query) {
                            Some(i) => {
                                self.dash.page = i;
                                self.dash.status = format!("Showing {} for ‘{query}’.", PAGES[i].0);
                            }
                            None if query.is_empty() => {
                                self.dash.page = 0;
                                self.dash.status.clear();
                            }
                            None => self.dash.status = format!("No setting matched ‘{query}’."),
                        }
                    }
                    ui.add_space(14.0);
                    ui.label(RichText::new("CATEGORIES").family(egui::FontFamily::Name("bold".into())).size(9.0).color(muted(ui)));
                    ui.add_space(6.0);
                    for (i, (name, badge, _)) in PAGES.iter().enumerate() {
                        let selected = self.dash.page == i;
                        let label = if *name == "Download" { *name } else { crate::i18n::tr(name) };
                        let text = RichText::new(format!("{badge}   {label}"))
                            .family(egui::FontFamily::Name("bold".into()))
                            .size(11.0)
                            .color(if selected { p.fg } else { muted(ui) });
                        let button = egui::Button::new(text)
                            .fill(if selected { p.surface2 } else { egui::Color32::TRANSPARENT })
                            .stroke(if selected {
                                egui::Stroke::new(1.0, p.border)
                            } else {
                                egui::Stroke::NONE
                            })
                            .corner_radius(8.0)
                            .min_size(egui::vec2(ui.available_width(), 38.0));
                        if ui.add(button).clicked() {
                            self.dash.page = i;
                        }
                        ui.add_space(2.0);
                    }
                    ui.with_layout(egui::Layout::bottom_up(egui::Align::Min), |ui| {
                        let guide = egui::Button::new(RichText::new("?   Open Guide").size(11.0).color(muted(ui)))
                            .fill(egui::Color32::TRANSPARENT)
                            .stroke(egui::Stroke::NONE)
                            .min_size(egui::vec2(ui.available_width(), 36.0));
                        if ui.add(guide).clicked() {
                            self.show_help = true;
                        }
                    });
                });
            egui::CentralPanel::default()
                .frame(Frame::new().fill(p.bg).inner_margin(Margin {
                    left: 22,
                    right: 14,
                    top: 12,
                    bottom: 12,
                }))
                .show(ui, |ui| {
                    {
                        let st = ui.style_mut();
                        st.spacing.button_padding = egui::vec2(14.0, 6.0);
                        st.spacing.interact_size.y = 30.0;
                        st.spacing.item_spacing.y = 6.0;
                        st.visuals.widgets.inactive.bg_stroke = egui::Stroke::new(1.0, p.border);
                        st.override_font_id = None;
                    }
                    egui::ScrollArea::vertical()
                        .id_salt(("settings_page", self.dash.page))
                        .auto_shrink([false, false])
                        .show(ui, |ui| {
                            ui.set_width(ui.available_width() - 14.0);
                            let page = self.dash.page.min(PAGE_TEXT.len() - 1);
                            let (heading, desc) = PAGE_TEXT[page];
                            title(ui, heading, desc);
                            match page {
                                0 => {
                                    new_pref = self.dash.general_cards(ui, &path, &vctx, &self.auto_pref);
                                }
                                1 => self.dash.network_card(ui, &mut self.settings.net),
                                2 => {
                                    if let Some(ai) = &mut self.ai_panel {
                                        ai.inline(ui, &vctx);
                                        if let Some(s) = ai.updated.take() {
                                            self.ai_session = s;
                                        }
                                    }
                                    self.dash.integration_cards(ui, &path, &vctx);
                                    crate::settings_dashboard::boxed(
                                        ui,
                                        "LIB",
                                        crate::settings_dashboard::rgb(0x6d, 0x28, 0xd9),
                                        "CYOA Manager",
                                        "Add finished downloads to the CYOA Manager library.",
                                        |ui| self.manager.ui(ui),
                                    );
                                }
                                3 => {
                                    if let Some(v) = &mut self.viewers {
                                        v.inline(ui, &vctx);
                                    }
                                }
                                _ => self.dash.maintenance_cards(ui, &path, &vctx),
                            }
                        });
                });
        });
        if let Some(v) = new_pref {
            self.auto_pref = v.to_owned();
            self.save_shared_key("auto_detect_output", v.into());
        }
        if std::mem::take(&mut self.dash.open_updates) && self.updates.is_none() {
            self.updates = Some(crate::updates_panel::UpdatesPanel::new());
        }
        if let Some(mode) = self.dash.cf_mode_changed.take() {
            self.cf_mode = mode;
        }
        if close {
            self.show_settings = false;
            self.ai_panel = None;
            self.viewers = None;
            self.dash.closed();
        }
    }
}
