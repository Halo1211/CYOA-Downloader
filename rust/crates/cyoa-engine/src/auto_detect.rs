//! Python `project.discover._detect_auto` / `_auto_detect_output_variant`:
//! choose the concrete output mode for a queue row in "Auto (detect)" mode.
//! cyoa.cafe catalogue URLs are resolved first; CYOAP Vue is accepted only
//! when its platform/node JSON validates; everything else uses ICC website
//! output in the folder or ZIP variant from `auto_detect_output`.

use cyoa_net::client::FetchClient;
use tokio_util::sync::CancellationToken;

/// Concrete mode chosen by Auto (Python mode strings).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AutoDetection {
    pub mode: &'static str,
    pub effective_url: String,
    pub reason: &'static str,
}

/// Python `_normalize_auto_detect_output`: "zip" or "folder".
pub fn normalize_pref(pref: &str) -> &'static str {
    if pref.trim().eq_ignore_ascii_case("zip") {
        "zip"
    } else {
        "folder"
    }
}

/// Python `_auto_detect_output_variant`.
pub fn output_variant(kind: &str, pref: &str) -> &'static str {
    let zip = normalize_pref(pref) == "zip";
    match (kind.eq_ignore_ascii_case("cyoap_vue"), zip) {
        (true, true) => "cyoap_vue_zip",
        (true, false) => "cyoap_vue_folder",
        (false, true) => "website_zip",
        (false, false) => "website_folder",
    }
}

/// Saved `auto_detect_output` preference (default "folder").
pub fn saved_pref() -> &'static str {
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    let v = store
        .load()
        .ok()
        .and_then(|m| {
            m.get("auto_detect_output")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
        })
        .unwrap_or_default();
    normalize_pref(&v)
}

/// Python `_detect_auto(url)`.
pub async fn detect_auto(
    client: &FetchClient,
    url: &str,
    pref: &str,
    cancel: &CancellationToken,
) -> AutoDetection {
    let source =
        cyoa_core::url::canonicalize_url(url.trim()).unwrap_or_else(|_| url.trim().to_owned());
    let parsed = url::Url::parse(&source).ok();
    let host = parsed
        .as_ref()
        .and_then(|u| u.host_str())
        .unwrap_or("")
        .to_lowercase();
    let is_cafe = host == "cyoa.cafe" || host.ends_with(".cyoa.cafe");
    let is_cafe_metadata = host == "cyoa.cafe"
        && parsed
            .as_ref()
            .is_some_and(|u| u.path().trim_end_matches('/').starts_with("/game/"));
    tracing::info!(
        "[Auto-detect] Probing: {}",
        crate::website::js::directory_base_url(&source)
    );
    let website = output_variant("website", pref);
    let mut probe = source.clone();

    if is_cafe {
        let (c, h, k, u) = (
            client.clone(),
            tokio::runtime::Handle::current(),
            cancel.clone(),
            source.clone(),
        );
        let outcome = tokio::task::spawn_blocking(move || {
            let mut fetch = crate::discover_net::ClientCafeFetch {
                client: c,
                handle: h,
                cancel: k,
            };
            if is_cafe_metadata {
                let record = crate::cafe::fetch_cyoa_cafe_record(&mut fetch, &u, false);
                if crate::cafe::classify_cyoa_cafe_record(record.as_ref()) == "static_pages" {
                    return Ok(None);
                }
            }
            crate::cafe::CafeResolver::new(&mut fetch)
                .resolve(&u)
                .map(Some)
        })
        .await
        .unwrap_or_else(|e| Err(e.to_string()));
        match outcome {
            Ok(None) => {
                tracing::info!("[Auto-detect] → cyoa.cafe static-page record → {website}");
                return AutoDetection {
                    mode: website,
                    effective_url: source,
                    reason: "static_catalogue",
                };
            }
            Ok(Some(resolved)) => {
                if !resolved.is_empty() {
                    let resolved = cyoa_core::url::canonicalize_url(&resolved).unwrap_or(resolved);
                    if resolved != source {
                        tracing::info!("[Auto-detect] Resolved cyoa.cafe target → {resolved}");
                    }
                    probe = resolved;
                }
            }
            Err(e) => {
                tracing::warn!("[Auto-detect] cyoa.cafe resolver unavailable: {e}");
                if is_cafe_metadata {
                    tracing::info!("[Auto-detect] → unresolved metadata page; using {website}");
                    return AutoDetection {
                        mode: website,
                        effective_url: source,
                        reason: "unresolved_catalogue",
                    };
                }
            }
        }
    }

    let base = crate::website::js::directory_base_url(&probe);
    if crate::cyoap::probe_cyoap_vue_structure(client, &base, cancel)
        .await
        .is_some()
    {
        let mode = output_variant("cyoap_vue", pref);
        tracing::info!("[Auto-detect] → cyoap_vue detected → {mode}");
        return AutoDetection {
            mode,
            effective_url: probe,
            reason: "cyoap_structure",
        };
    }
    tracing::info!("[Auto-detect] → defaulting to {website}");
    AutoDetection {
        mode: website,
        effective_url: probe,
        reason: "website_fallback",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn variants_follow_python() {
        assert_eq!(output_variant("cyoap_vue", "zip"), "cyoap_vue_zip");
        assert_eq!(output_variant("CYOAP_VUE", "folder"), "cyoap_vue_folder");
        assert_eq!(output_variant("website", " ZIP "), "website_zip");
        assert_eq!(output_variant("website", "bogus"), "website_folder");
    }
}
