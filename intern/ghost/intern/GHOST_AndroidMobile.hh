/* SPDX-FileCopyrightText: 2026 Blender Authors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <unordered_map>
#include <vector>

/* Main-thread only. Coordinates are scaled GHOST pixels (origin at top left).
 * A pointer belongs to a control until UP, even after a resize/disable cancels
 * its axes. No late release may become a Blender click. */
class GHOST_AndroidMobile {
 public:
  using Layout = std::array<float, 12>; /* id, bounds[4], move[2], look[2], radius, frame[2] */
  using State = std::array<float, 6>; /* id, move[2], look[2], frame */
 private:
  struct Owner { int region, control; };
  std::vector<Layout> layouts_;
  std::unordered_map<int, Owner> owners_;
  std::unordered_map<int, State> states_;
  const Layout *find(int id) const
  {
    for (const Layout &l : layouts_) { if (int(l[0]) == id) { return &l; } }
    return nullptr;
  }
  static bool circle(float x, float y, float cx, float cy, float r)
  {
    return std::hypot(x - cx, y - cy) <= r;
  }
 public:
  void cancel()
  {
    states_.clear();
    for (auto &entry : owners_) { entry.second.region = -1; }
  }
  void layout(const float *rows, int count)
  {
    std::vector<Layout> next;
    for (int i = 0; i < count; i++) {
      Layout l;
      std::copy_n(rows + i * 12, 12, l.begin());
      next.push_back(l);
    }
    if (next != layouts_) { cancel(); layouts_ = std::move(next); }
  }
  bool owns(int pointer) const { return owners_.count(pointer) != 0; }
  bool inside(float x, float y) const
  {
    for (const Layout &l : layouts_) {
      if (x >= l[1] && y >= l[2] && x <= l[3] && y <= l[4]) { return true; }
    }
    return false;
  }
  bool down(int pointer, float x, float y)
  {
    if (owns(pointer)) { return true; }
    for (const Layout &l : layouts_) {
      if (!inside_region(l, x, y)) { continue; }
      for (int c = 0; c < 3; c++) {
        const int offset = c == 2 ? 10 : 5 + 2 * c;
        const float radius = l[9] * (c == 2 ? 0.48f : 1.0f);
        if (!circle(x, y, l[offset], l[offset + 1], radius)) { continue; }
        bool busy = false;
        for (const auto &entry : owners_) {
          busy |= entry.second.region == int(l[0]) && entry.second.control == c;
        }
        owners_[pointer] = {busy ? -1 : int(l[0]), c};
        move(pointer, x, y);
        return true;
      }
    }
    return false;
  }
  static bool inside_region(const Layout &l, float x, float y)
  {
    return x >= l[1] && y >= l[2] && x <= l[3] && y <= l[4];
  }
  void move(int pointer, float x, float y)
  {
    auto it = owners_.find(pointer);
    if (it == owners_.end() || it->second.region < 0 || it->second.control == 2) { return; }
    const Owner o = it->second;
    const Layout *l = find(o.region);
    if (!l) { return; }
    const int offset = 5 + 2 * o.control;
    float dx = (x - (*l)[offset]) / (*l)[9];
    float dy = ((*l)[offset + 1] - y) / (*l)[9];
    const float length = std::hypot(dx, dy);
    const float magnitude = std::clamp((length - 0.1f) / 0.9f, 0.0f, 1.0f);
    if (length > 0) { dx *= magnitude / length; dy *= magnitude / length; }
    State &s = states_[o.region];
    s[0] = float(o.region);
    s[1 + 2 * o.control] = dx;
    s[2 + 2 * o.control] = dy;
  }
  void up(int pointer, float x, float y)
  {
    auto it = owners_.find(pointer);
    if (it == owners_.end()) { return; }
    const Owner o = it->second;
    const Layout *l = find(o.region);
    if (l && o.region >= 0) {
      State &s = states_[o.region];
      s[0] = float(o.region);
      if (o.control == 2) {
        if (circle(x, y, (*l)[10], (*l)[11], (*l)[9] * 0.48f)) { s[5] = 1; }
      }
      else { s[1 + 2 * o.control] = s[2 + 2 * o.control] = 0; }
    }
    owners_.erase(it);
  }
  int state(float *rows)
  {
    int i = 0;
    for (auto &entry : states_) {
      std::copy(entry.second.begin(), entry.second.end(), rows + i++ * 6);
      entry.second[5] = 0;
    }
    return i;
  }
};
