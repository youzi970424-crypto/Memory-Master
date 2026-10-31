// Geometry uses one consistent desktop coordinate space, including negative origins.
pub fn snap_position(
    position: (i32, i32),
    old_size: (u32, u32),
    new_size: (u32, u32),
    area: (i32, i32, u32, u32),
) -> (i32, i32) {
    let (left, top, width, height) = area;
    let right = left + width as i32;
    let bottom = top + height as i32;
    let x = position.0.clamp(left, (right - old_size.0 as i32).max(left));
    let y = position.1.clamp(top, (bottom - old_size.1 as i32).max(top));
    let distances = [
        (x - left).abs(),
        (right - x - old_size.0 as i32).abs(),
        (y - top).abs(),
        (bottom - y - old_size.1 as i32).abs(),
    ];
    let edge = distances.iter().enumerate().min_by_key(|(_, distance)| *distance).unwrap().0;
    let max_x = (right - new_size.0 as i32).max(left);
    let max_y = (bottom - new_size.1 as i32).max(top);
    match edge {
        0 => (left, y.clamp(top, max_y)),
        1 => (max_x, y.clamp(top, max_y)),
        2 => (x.clamp(left, max_x), top),
        _ => (x.clamp(left, max_x), max_y),
    }
}

#[cfg(test)]
mod tests {
    use super::snap_position;

    #[test]
    fn snaps_to_each_nearest_edge() {
        let area = (0, 24, 1440, 850);
        for (position, expected) in [
            ((100, 400), (0, 400)), ((1300, 400), (1384, 400)),
            ((700, 100), (700, 24)), ((700, 780), (700, 818)),
        ] {
            assert_eq!(snap_position(position, (56, 56), (56, 56), area), expected);
        }
    }

    #[test]
    fn expansion_and_collapse_preserve_edges() {
        let area = (-2560, -200, 2560, 1440);
        for anchor in [(-2560, 300), (-112, 300), (-1200, -200), (-1200, 1128)] {
            let expanded = snap_position(anchor, (112, 112), (328, 112), area);
            assert_eq!(snap_position(expanded, (328, 112), (112, 112), area), anchor);
            assert!(expanded.0 >= -2560 && expanded.0 + 328 <= 0);
        }
    }

    #[test]
    fn clamps_offscreen_positions_and_large_notice() {
        assert_eq!(snap_position((1900, 500), (56, 56), (56, 56), (0, 24, 1440, 850)), (1384, 500));
        let p = snap_position((1384, 818), (56, 56), (320, 180), (0, 24, 1440, 850));
        assert!(p.0 >= 0 && p.0 + 320 <= 1440 && p.1 >= 24 && p.1 + 180 <= 874);
    }
}
