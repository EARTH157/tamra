import math

import numpy as np

from tamra.ocr.geometry import (
    box_score,
    components,
    crop_quad,
    min_area_rect,
    order_clockwise,
    resize_bilinear,
    run_points,
    slice_box,
    sort_boxes,
    unclip,
)


def test_components_are_four_connected_row_runs():
    mask = np.zeros((6, 8), bool)
    mask[1, 1:4] = mask[2, 2] = True
    mask[4, 5:8] = True
    mask[3, 4] = True  # touches nothing 4-connected
    bounds = sorted(
        (int(r[:, 1].min()), int(r[:, 0].min()), int(r[:, 2].max()), int(r[:, 0].max()) + 1)
        for r in components(mask)
    )
    assert bounds == [(1, 1, 4, 3), (4, 3, 5, 4), (5, 4, 8, 5)]


def test_min_area_rect_of_an_axis_aligned_block():
    mask = np.zeros((20, 40), bool)
    mask[5:9, 10:30] = True
    (runs,) = components(mask)
    box, short = min_area_rect(run_points(runs))
    assert np.allclose(box, [[10, 5], [29, 5], [29, 8], [10, 8]])
    assert short == 3.0


def test_min_area_rect_follows_a_tilted_line():
    pts = np.array([[x, 10 + x * math.tan(math.radians(5))] for x in range(0, 100)])
    pts = np.concatenate([pts, pts + [0, 6]])
    box, short = min_area_rect(pts)
    top = box[1] - box[0]
    assert abs(math.degrees(math.atan2(top[1], top[0])) - 5) < 0.5
    assert 5.5 < short < 6.5


def test_box_score_is_the_mean_inside_the_box():
    prob = np.zeros((10, 10), np.float32)
    prob[2:5, 2:7] = 1.0
    box = np.array([[2, 2], [6, 2], [6, 4], [2, 4]], np.float64)
    assert box_score(prob, box) == 1.0
    wide = np.array([[0, 2], [9, 2], [9, 4], [0, 4]], np.float64)
    assert box_score(prob, wide) == 0.5


def test_unclip_grows_every_side_by_area_ratio_over_perimeter():
    box = np.array([[0, 0], [100, 0], [100, 20], [0, 20]], np.float64)
    grown, short = min_area_rect(unclip(box, 1.6))  # d = 100 * 20 * 1.6 / 240 = 13.33 -> 13
    assert np.allclose(grown, [[-13, -13], [113, -13], [113, 33], [-13, 33]])
    assert short == 46.0


def test_order_clockwise_and_reading_order():
    box = np.array([[10, 8], [0, 0], [10, 0], [0, 8]], np.float64)
    assert order_clockwise(box).tolist() == [[0, 0], [10, 0], [10, 8], [0, 8]]
    a = order_clockwise(np.array([[50, 3], [90, 3], [90, 9], [50, 9]], np.float64))
    b = order_clockwise(np.array([[0, 0], [40, 0], [40, 8], [0, 8]], np.float64))
    c = order_clockwise(np.array([[0, 30], [40, 30], [40, 38], [0, 38]], np.float64))
    assert [x[0, 0] for x in sort_boxes([c, a, b])] == [0, 50, 0]


def test_crop_quad_takes_an_upright_rectangle_exactly():
    img = np.zeros((20, 30, 3), np.uint8)
    img[5:10, 10:20] = 255
    crop, turned = crop_quad(img, np.array([[10, 5], [20, 5], [20, 10], [10, 10]], np.float64))
    assert crop.shape == (5, 10, 3) and crop.min() == 255 and not turned


def test_crop_quad_turns_a_tall_box():
    img = np.full((40, 40), 200, np.uint8)
    crop, turned = crop_quad(img, np.array([[5, 5], [10, 5], [10, 25], [5, 25]], np.float64))
    assert turned and crop.shape == (5, 20)


def test_crop_quad_straightens_a_tilted_bar():
    img = np.full((60, 120), 255, np.uint8)
    t = math.radians(3)
    for x in range(10, 110):
        y0 = 20 + (x - 10) * math.tan(t)
        img[int(round(y0)) : int(round(y0)) + 10, x] = 0
    c, s = math.cos(t), math.sin(t)
    p0 = np.array([10.0, 20.0])
    u, v = np.array([c, s]) * 100, np.array([-s, c]) * 10
    crop, _ = crop_quad(img, np.array([p0, p0 + u, p0 + u + v, p0 + v]))
    assert crop.shape == (10, 100)
    assert crop[2:8, 5:95].mean() < 40  # the bar fills the middle rows of the crop


def test_slice_box_of_an_upright_box():
    box = np.array([[0, 10], [100, 10], [100, 30], [0, 30]], np.float64)
    assert slice_box(box, 0.25, 0.5) == (25.0, 10.0, 50.0, 30.0)


def test_resize_bilinear_keeps_constant_images():
    img = np.full((7, 9, 3), 77, np.uint8)
    assert np.allclose(resize_bilinear(img, 3, 4), 77)
    assert resize_bilinear(img[:, :, 0], 14, 18).shape == (14, 18)


def test_unclip_rounds_halves_away_from_zero_like_clipper():
    box = np.array([[10, 10], [15, 10], [15, 13], [10, 13]], np.float64)
    grown, _ = min_area_rect(unclip(box, 1.6))  # d = 1.5: every side lands on a .5
    assert np.allclose(grown, [[9, 9], [17, 9], [17, 15], [9, 15]])  # pyclipper's result


def test_crop_quad_survives_a_degenerate_box():
    img = np.full((20, 20), 128, np.uint8)
    crop, turned = crop_quad(img, np.array([[0, 0], [10, 0], [10, 0], [0, 8]], np.float64))
    assert crop.shape == (8, 12) and not turned


def test_resize_bilinear_interpolates_like_opencv():
    out = resize_bilinear(np.array([[0, 100]], np.uint8), 1, 4)
    assert np.allclose(out, [[0, 25, 75, 100]])
