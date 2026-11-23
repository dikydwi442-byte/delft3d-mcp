use byteorder::{BigEndian, ByteOrder, LittleEndian};
use memmap2::Mmap;
use std::collections::HashMap;
use std::fs::File;
use std::path::Path;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum NefisError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Invalid NEFIS file: {0}")]
    Format(String),
    #[error("Group or element not found: {0}")]
    NotFound(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Endianness {
    Little,
    Big,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AddrSize {
    U32,
    U64,
}

impl AddrSize {
    #[must_use]
    pub const fn bytes(self) -> usize {
        match self {
            Self::U32 => 4,
            Self::U64 => 8,
        }
    }

    #[must_use]
    pub const fn nil(self) -> u64 {
        match self {
            Self::U32 => u32::MAX as u64,
            Self::U64 => u64::MAX,
        }
    }
}

#[derive(Debug, Clone)]
pub enum ElmType {
    Character,
    Complex,
    Integer,
    Logical,
    Real,
    Unknown(String),
}

#[derive(Debug, Clone)]
pub struct ElmDef {
    pub name: String,
    pub elm_type: ElmType,
    pub elm_size_bytes: u64,
    pub val_size_bytes: u32,
    pub quantity: String,
    pub units: String,
    pub description: String,
    pub dims: Vec<u32>,
}

#[derive(Debug, Clone)]
pub struct CelDef {
    pub name: String,
    pub cell_size_bytes: u64,
    pub element_names: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct GrpDef {
    pub name: String,
    pub cel_name: String,
    pub dims: Vec<u32>,
    pub order: Vec<u32>,
}

#[derive(Debug, Clone)]
pub struct GrpDat {
    pub offset: u64,
    pub var_dim: bool,
    pub var_dim_idx: Option<usize>,
    pub name: String,
    pub def_name: String,
    pub data_size: u64,
    pub resolved_dims: Vec<u32>,
}

#[derive(Debug, Clone)]
pub struct NefisGroupInfo {
    pub name: String,
    pub def_name: String,
    pub cel_name: String,
    pub var_dim: bool,
    pub dims: Vec<u32>,
    pub elements: Vec<String>,
}

#[derive(Debug, Clone)]
pub enum NefisValues {
    Float64(Vec<f64>),
    Int64(Vec<i64>),
    Strings(Vec<String>),
}

#[derive(Debug, Clone)]
pub struct NefisDataset {
    pub group: String,
    pub element: String,
    pub units: String,
    pub description: String,
    pub shape: Vec<usize>,
    pub values: NefisValues,
}

pub struct NefisFile {
    dat_map: Mmap,
    def_map: Option<Mmap>,
    onefile: bool,
    endian: Endianness,
    addr: AddrSize,
    pub groups: Vec<GrpDat>,
    pub grp_defs: HashMap<String, GrpDef>,
    pub cel_defs: HashMap<String, CelDef>,
    pub elm_defs: HashMap<String, ElmDef>,
}

fn read_u32(buf: &[u8], off: usize, endian: Endianness) -> Result<u32, NefisError> {
    if off + 4 > buf.len() {
        return Err(NefisError::Format("Unexpected EOF reading u32".to_string()));
    }
    Ok(match endian {
        Endianness::Little => LittleEndian::read_u32(&buf[off..off + 4]),
        Endianness::Big => BigEndian::read_u32(&buf[off..off + 4]),
    })
}

fn read_i32(buf: &[u8], off: usize, endian: Endianness) -> Result<i32, NefisError> {
    if off + 4 > buf.len() {
        return Err(NefisError::Format("Unexpected EOF reading i32".to_string()));
    }
    Ok(match endian {
        Endianness::Little => LittleEndian::read_i32(&buf[off..off + 4]),
        Endianness::Big => BigEndian::read_i32(&buf[off..off + 4]),
    })
}

fn read_f32(buf: &[u8], off: usize, endian: Endianness) -> Result<f32, NefisError> {
    if off + 4 > buf.len() {
        return Err(NefisError::Format("Unexpected EOF reading f32".to_string()));
    }
    Ok(match endian {
        Endianness::Little => LittleEndian::read_f32(&buf[off..off + 4]),
        Endianness::Big => BigEndian::read_f32(&buf[off..off + 4]),
    })
}

fn read_f64(buf: &[u8], off: usize, endian: Endianness) -> Result<f64, NefisError> {
    if off + 8 > buf.len() {
        return Err(NefisError::Format("Unexpected EOF reading f64".to_string()));
    }
    Ok(match endian {
        Endianness::Little => LittleEndian::read_f64(&buf[off..off + 8]),
        Endianness::Big => BigEndian::read_f64(&buf[off..off + 8]),
    })
}

fn read_addr(
    buf: &[u8],
    off: usize,
    endian: Endianness,
    addr: AddrSize,
) -> Result<u64, NefisError> {
    match addr {
        AddrSize::U32 => Ok(u64::from(read_u32(buf, off, endian)?)),
        AddrSize::U64 => {
            if off + 8 > buf.len() {
                return Err(NefisError::Format("Unexpected EOF reading u64".to_string()));
            }
            Ok(match endian {
                Endianness::Little => LittleEndian::read_u64(&buf[off..off + 8]),
                Endianness::Big => BigEndian::read_u64(&buf[off..off + 8]),
            })
        }
    }
}

fn read_ascii_trimmed(buf: &[u8], off: usize, len: usize) -> String {
    let end = (off + len).min(buf.len());
    if off >= end {
        return String::new();
    }
    String::from_utf8_lossy(&buf[off..end])
        .trim_end_matches([' ', '\0'])
        .to_string()
}

fn detect_format(buf: &[u8], onefile: bool) -> Result<(Endianness, AddrSize, usize), NefisError> {
    let header_len = if onefile { 128 } else { 60 };
    if buf.len() < header_len + 8 {
        return Err(NefisError::Format(
            "File too short for NEFIS header".to_string(),
        ));
    }
    let header = String::from_utf8_lossy(&buf[..header_len]);
    let last_char = buf[header_len - 1] as char;

    let addr = if header.contains("Version 4.") || header.contains("Versie 1.") {
        AddrSize::U32
    } else if header.contains("File; 5.") || last_char == 'L' || last_char == 'B' {
        AddrSize::U64
    } else {
        AddrSize::U32
    };

    let endian = match last_char {
        'L' => Endianness::Little,
        'B' | 'N' => Endianness::Big,
        _ => {
            let fsize = buf.len() as u64;
            let le_sz = read_addr(buf, header_len, Endianness::Little, addr)?;
            let be_sz = read_addr(buf, header_len, Endianness::Big, addr)?;
            if le_sz == fsize {
                Endianness::Little
            } else if be_sz == fsize {
                Endianness::Big
            } else {
                Endianness::Little
            }
        }
    };

    Ok((endian, addr, header_len + addr.bytes()))
}

fn read_hash_buckets(
    buf: &[u8],
    table_off: usize,
    endian: Endianness,
    addr: AddrSize,
) -> Result<Vec<u64>, NefisError> {
    let nil = addr.nil();
    let ab = addr.bytes();
    let mut offsets = Vec::new();

    for i in 0..997 {
        let off = read_addr(buf, table_off + i * ab, endian, addr)?;
        if off != nil && (off as usize) < buf.len() {
            offsets.push(off);
        }
    }

    let mut idx = 0;
    while idx < offsets.len() {
        let cur = offsets[idx] as usize;
        if cur + ab <= buf.len() {
            let next_link = read_addr(buf, cur, endian, addr)?;
            if next_link != nil && (next_link as usize) < buf.len() && !offsets.contains(&next_link)
            {
                offsets.push(next_link);
            }
        }
        idx += 1;
    }

    offsets.sort_unstable();
    offsets.dedup();
    Ok(offsets)
}

impl NefisFile {
    pub fn open(dat_path: &Path, def_path: Option<&Path>) -> Result<Self, NefisError> {
        let dat_file = File::open(dat_path)?;
        // SAFETY: read-only mapping of immutable or closed NEFIS simulation output
        #[allow(unsafe_code)]
        let dat_map = unsafe { Mmap::map(&dat_file)? };

        let header_probe =
            String::from_utf8_lossy(&dat_map[..dat_map.len().min(128)]).to_lowercase();
        let onefile = header_probe.contains("definition and data");

        let (endian, addr, dat_tables_start) = detect_format(&dat_map, onefile)?;
        let ab = addr.bytes();

        let def_map = if onefile {
            None
        } else {
            let resolved_def = if let Some(p) = def_path {
                p.to_path_buf()
            } else {
                dat_path.with_extension("def")
            };
            let df = File::open(&resolved_def)?;
            // SAFETY: read-only mapping of immutable NEFIS definition file
            #[allow(unsafe_code)]
            Some(unsafe { Mmap::map(&df)? })
        };

        let def_buf: &[u8] = def_map.as_deref().unwrap_or(&dat_map);
        let def_tables_start = if onefile {
            dat_tables_start
        } else {
            let (_, _, d_start) = detect_format(def_buf, false)?;
            d_start
        };

        // 1. Read DAT group records
        let grp_dat_table_off = if onefile {
            dat_tables_start + 3 * ab * 997
        } else {
            dat_tables_start
        };
        let grp_dat_offsets = read_hash_buckets(&dat_map, grp_dat_table_off, endian, addr)?;
        let mut groups = Vec::with_capacity(grp_dat_offsets.len());

        for off in grp_dat_offsets {
            let o = off as usize;
            if o + 3 * ab + 392 > dat_map.len() {
                continue;
            }
            let rec_size = read_addr(&dat_map, o + ab, endian, addr)?;
            let code = &dat_map[o + 2 * ab..o + 3 * ab];
            let var_dim = code.last() == Some(&b'5');
            let name = read_ascii_trimmed(&dat_map, o + 3 * ab, 16);
            let def_name = read_ascii_trimmed(&dat_map, o + 3 * ab + 16, 16);

            let data_size = if var_dim {
                read_addr(&dat_map, o + 3 * ab + 392, endian, addr)?
            } else {
                rec_size.saturating_sub(392 + 3 * (ab as u64))
            };

            groups.push(GrpDat {
                offset: off,
                var_dim,
                var_dim_idx: None,
                name,
                def_name,
                data_size,
                resolved_dims: Vec::new(),
            });
        }

        // 2. Read DEF: Elements
        let elm_offsets = read_hash_buckets(def_buf, def_tables_start, endian, addr)?;
        let mut elm_defs = HashMap::new();
        for off in elm_offsets {
            let o = off as usize;
            let min_sz = 3 * ab + 24 + ab + 4 + 96 + 24;
            if o + min_sz > def_buf.len() {
                continue;
            }
            let name = read_ascii_trimmed(def_buf, o + 3 * ab, 16);
            let typ_str = read_ascii_trimmed(def_buf, o + 3 * ab + 16, 8);
            let elm_type = match typ_str.as_str() {
                "CHARACTE" => ElmType::Character,
                "COMPLEX" => ElmType::Complex,
                "INTEGER" => ElmType::Integer,
                "LOGICAL" => ElmType::Logical,
                "REAL" => ElmType::Real,
                other => ElmType::Unknown(other.to_string()),
            };
            let mut p = o + 3 * ab + 24;
            let elm_size_bytes = read_addr(def_buf, p, endian, addr)?;
            p += ab;
            let val_size_bytes = read_u32(def_buf, p, endian)?;
            p += 4;
            let quantity = read_ascii_trimmed(def_buf, p, 16);
            let units = read_ascii_trimmed(def_buf, p + 16, 16);
            let description = read_ascii_trimmed(def_buf, p + 32, 64);
            p += 96;
            let ndim_raw = read_u32(def_buf, p, endian)?;
            let ndim = if ndim_raw == 0 {
                1
            } else {
                ndim_raw.min(5) as usize
            };
            let mut dims = Vec::with_capacity(ndim);
            for d in 0..ndim {
                dims.push(read_u32(def_buf, p + 4 + d * 4, endian)?);
            }
            elm_defs.insert(
                name.clone(),
                ElmDef {
                    name,
                    elm_type,
                    elm_size_bytes,
                    val_size_bytes,
                    quantity,
                    units,
                    description,
                    dims,
                },
            );
        }

        // 3. Read DEF: Cells
        let cel_offsets = read_hash_buckets(def_buf, def_tables_start + ab * 997, endian, addr)?;
        let mut cel_defs = HashMap::new();
        for off in cel_offsets {
            let o = off as usize;
            if o + 4 * ab + 20 > def_buf.len() {
                continue;
            }
            let name = read_ascii_trimmed(def_buf, o + 3 * ab, 16);
            let cell_size_bytes = read_addr(def_buf, o + 3 * ab + 16, endian, addr)?;
            let num_elm = read_u32(def_buf, o + 4 * ab + 16, endian)? as usize;
            let mut element_names = Vec::with_capacity(num_elm);
            let elms_start = o + 4 * ab + 20;
            for k in 0..num_elm {
                if elms_start + (k + 1) * 16 <= def_buf.len() {
                    element_names.push(read_ascii_trimmed(def_buf, elms_start + k * 16, 16));
                }
            }
            cel_defs.insert(
                name.clone(),
                CelDef {
                    name,
                    cell_size_bytes,
                    element_names,
                },
            );
        }

        // 4. Read DEF: Groups & resolve var-dim counts
        let grp_def_offsets =
            read_hash_buckets(def_buf, def_tables_start + 2 * ab * 997, endian, addr)?;
        let mut grp_defs = HashMap::new();
        for off in grp_def_offsets {
            let o = off as usize;
            if o + 3 * ab + 32 + 44 > def_buf.len() {
                continue;
            }
            let name = read_ascii_trimmed(def_buf, o + 3 * ab, 16);
            let cel_name = read_ascii_trimmed(def_buf, o + 3 * ab + 16, 16);
            let p = o + 3 * ab + 32;
            let ndim_raw = read_u32(def_buf, p, endian)?;
            let ndim = if ndim_raw == 0 {
                1
            } else {
                ndim_raw.min(5) as usize
            };
            let mut dims = Vec::with_capacity(ndim);
            let mut order = Vec::with_capacity(ndim);
            for d in 0..ndim {
                dims.push(read_u32(def_buf, p + 4 + d * 4, endian)?);
                order.push(read_u32(def_buf, p + 24 + d * 4, endian)?);
            }
            grp_defs.insert(
                name.clone(),
                GrpDef {
                    name,
                    cel_name,
                    dims,
                    order,
                },
            );
        }

        // Resolve variable dimension sizes via 4-level 256-entry pointer tree
        let nil = addr.nil();
        for g in &mut groups {
            if let Some(gd) = grp_defs.get(&g.def_name) {
                let mut dims = gd.dims.clone();
                if let Some(vidx) = dims.iter().position(|&x| x == 0) {
                    g.var_dim_idx = Some(vidx);
                    let mut cur_ptr_table = (g.offset as usize) + 392 + 4 * ab;
                    let mut total_vd = 0_usize;
                    for nbyte in (0..=3).rev() {
                        if cur_ptr_table + 256 * ab > dat_map.len() {
                            break;
                        }
                        let mut last_non_nil = None;
                        for k in 0..256 {
                            let val = read_addr(&dat_map, cur_ptr_table + k * ab, endian, addr)?;
                            if val != nil {
                                last_non_nil = Some((k, val));
                            }
                        }
                        let Some((k, next_off)) = last_non_nil else {
                            break;
                        };
                        if nbyte > 0 {
                            total_vd += k * 256_usize.pow(nbyte);
                            cur_ptr_table = next_off as usize;
                        } else {
                            total_vd += k;
                        }
                    }
                    dims[vidx] = total_vd as u32;
                }
                g.resolved_dims = dims;
            }
        }

        Ok(Self {
            dat_map,
            def_map,
            onefile,
            endian,
            addr,
            groups,
            grp_defs,
            cel_defs,
            elm_defs,
        })
    }

    #[must_use]
    pub fn list_groups(&self) -> Vec<NefisGroupInfo> {
        self.groups
            .iter()
            .map(|g| {
                let cel_name = self
                    .grp_defs
                    .get(&g.def_name)
                    .map_or_else(String::new, |d| d.cel_name.clone());
                let elements = self
                    .cel_defs
                    .get(&cel_name)
                    .map_or_else(Vec::new, |c| c.element_names.clone());
                NefisGroupInfo {
                    name: g.name.clone(),
                    def_name: g.def_name.clone(),
                    cel_name,
                    var_dim: g.var_dim,
                    dims: g.resolved_dims.clone(),
                    elements,
                }
            })
            .collect()
    }

    fn resolve_vd_offset(
        &self,
        grp: &GrpDat,
        vd_index_0based: usize,
    ) -> Result<Option<u64>, NefisError> {
        let ab = self.addr.bytes();
        let nil = self.addr.nil();
        let vd_1based = vd_index_0based + 1;
        let b0 = vd_1based & 0xFF;
        let b1 = (vd_1based >> 8) & 0xFF;
        let b2 = (vd_1based >> 16) & 0xFF;
        let b3 = (vd_1based >> 24) & 0xFF;
        let mut table_off = (grp.offset as usize) + 392 + 4 * ab;
        for b in [b3, b2, b1] {
            let next = read_addr(&self.dat_map, table_off + b * ab, self.endian, self.addr)?;
            if next == nil || (next as usize) >= self.dat_map.len() {
                return Ok(None);
            }
            table_off = next as usize;
        }
        let leaf = read_addr(&self.dat_map, table_off + b0 * ab, self.endian, self.addr)?;
        if leaf == nil {
            Ok(None)
        } else {
            Ok(Some(leaf))
        }
    }

    pub fn read_element(
        &self,
        group_name: &str,
        element_name: &str,
        vd_slice: Option<(usize, usize)>,
    ) -> Result<NefisDataset, NefisError> {
        let _ = (&self.def_map, self.onefile);
        let grp = self
            .groups
            .iter()
            .find(|g| g.name == group_name)
            .ok_or_else(|| NefisError::NotFound(format!("Group '{group_name}'")))?;
        let grp_def = self
            .grp_defs
            .get(&grp.def_name)
            .ok_or_else(|| NefisError::NotFound(format!("GroupDef '{}'", grp.def_name)))?;
        let cel_def = self
            .cel_defs
            .get(&grp_def.cel_name)
            .ok_or_else(|| NefisError::NotFound(format!("CellDef '{}'", grp_def.cel_name)))?;
        let elm_def = self
            .elm_defs
            .get(element_name)
            .ok_or_else(|| NefisError::NotFound(format!("Element '{element_name}'")))?;

        let mut elm_offset_in_cell = 0_usize;
        let mut found_in_cell = false;
        for en in &cel_def.element_names {
            if en == element_name {
                found_in_cell = true;
                break;
            }
            let ed = self
                .elm_defs
                .get(en)
                .ok_or_else(|| NefisError::NotFound(format!("Element '{en}' in cell")))?;
            elm_offset_in_cell += ed.elm_size_bytes as usize;
        }
        if !found_in_cell {
            return Err(NefisError::NotFound(format!(
                "Element '{element_name}' not in cell '{}'",
                cel_def.name
            )));
        }

        let cell_bytes = cel_def.cell_size_bytes as usize;
        let elm_bytes = elm_def.elm_size_bytes as usize;
        let val_bytes = elm_def.val_size_bytes as usize;
        let vals_per_elm = elm_bytes.checked_div(val_bytes).unwrap_or(1);

        // Determine fixed cell count per step & variable-dimension range
        let (vd_start, vd_end, fixed_cells_per_step) = if let Some(vidx) = grp.var_dim_idx {
            let vd_total = grp.resolved_dims[vidx] as usize;
            let (s, e) = vd_slice.unwrap_or((0, vd_total));
            let s_clamped = s.min(vd_total);
            let e_clamped = e.min(vd_total);
            let mut fixed = 1_usize;
            for (idx, &d) in grp.resolved_dims.iter().enumerate() {
                if idx != vidx {
                    fixed *= d.max(1) as usize;
                }
            }
            (s_clamped, e_clamped, fixed)
        } else {
            let mut fixed = 1_usize;
            for &d in &grp.resolved_dims {
                fixed *= d.max(1) as usize;
            }
            (0, 1, fixed)
        };

        let ab = self.addr.bytes();
        let fixed_base_off = (grp.offset as usize) + 392 + 3 * ab;

        let mut shape = Vec::new();
        if grp.var_dim_idx.is_some() {
            shape.push(vd_end.saturating_sub(vd_start));
        }
        for (idx, &d) in grp.resolved_dims.iter().enumerate() {
            if Some(idx) != grp.var_dim_idx && d > 1 {
                shape.push(d as usize);
            }
        }
        for &ed in &elm_def.dims {
            if ed > 1 || (shape.is_empty() && elm_def.dims.len() == 1) {
                shape.push(ed as usize);
            }
        }
        if shape.is_empty() {
            shape.push(1);
        }

        let total_items = vd_end.saturating_sub(vd_start) * fixed_cells_per_step * vals_per_elm;

        let values = match elm_def.elm_type {
            ElmType::Real => {
                let mut out = Vec::with_capacity(total_items);
                for vd in vd_start..vd_end {
                    let step_off = if grp.var_dim_idx.is_some() {
                        if let Some(o) = self.resolve_vd_offset(grp, vd)? {
                            o as usize
                        } else {
                            out.resize(out.len() + fixed_cells_per_step * vals_per_elm, f64::NAN);
                            continue;
                        }
                    } else {
                        fixed_base_off
                    };
                    for c in 0..fixed_cells_per_step {
                        let base = step_off + c * cell_bytes + elm_offset_in_cell;
                        for k in 0..vals_per_elm {
                            let p = base + k * val_bytes;
                            if val_bytes == 8 {
                                out.push(read_f64(&self.dat_map, p, self.endian)?);
                            } else {
                                out.push(f64::from(read_f32(&self.dat_map, p, self.endian)?));
                            }
                        }
                    }
                }
                NefisValues::Float64(out)
            }
            ElmType::Integer | ElmType::Logical => {
                let mut out = Vec::with_capacity(total_items);
                for vd in vd_start..vd_end {
                    let step_off = if grp.var_dim_idx.is_some() {
                        if let Some(o) = self.resolve_vd_offset(grp, vd)? {
                            o as usize
                        } else {
                            out.resize(out.len() + fixed_cells_per_step * vals_per_elm, 0);
                            continue;
                        }
                    } else {
                        fixed_base_off
                    };
                    for c in 0..fixed_cells_per_step {
                        let base = step_off + c * cell_bytes + elm_offset_in_cell;
                        for k in 0..vals_per_elm {
                            let p = base + k * val_bytes;
                            out.push(i64::from(read_i32(&self.dat_map, p, self.endian)?));
                        }
                    }
                }
                NefisValues::Int64(out)
            }
            ElmType::Character => {
                let str_count = vd_end.saturating_sub(vd_start) * fixed_cells_per_step;
                let mut out = Vec::with_capacity(str_count);
                for vd in vd_start..vd_end {
                    let step_off = if grp.var_dim_idx.is_some() {
                        if let Some(o) = self.resolve_vd_offset(grp, vd)? {
                            o as usize
                        } else {
                            out.push(String::new());
                            continue;
                        }
                    } else {
                        fixed_base_off
                    };
                    for c in 0..fixed_cells_per_step {
                        let base = step_off + c * cell_bytes + elm_offset_in_cell;
                        out.push(read_ascii_trimmed(&self.dat_map, base, elm_bytes));
                    }
                }
                NefisValues::Strings(out)
            }
            ElmType::Complex | ElmType::Unknown(_) => {
                return Err(NefisError::Format(format!(
                    "Unsupported NEFIS element type for '{element_name}'"
                )));
            }
        };

        Ok(NefisDataset {
            group: group_name.to_string(),
            element: element_name.to_string(),
            units: elm_def.units.clone(),
            description: elm_def.description.clone(),
            shape,
            values,
        })
    }
}
