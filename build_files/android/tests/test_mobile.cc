/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_AndroidMobile.hh"
#include <cassert>
#include <iostream>

int main()
{
  GHOST_AndroidMobile nav;
  float layout[] = {1, 0, 0, 1000, 700, 150, 550, 850, 550, 100, 500, 550};
  float state[32 * 6] = {};
  nav.layout(layout, 1);
  assert(!nav.down(0, 20, 30)); /* Other editor/viewport clicks pass through. */
  assert(nav.down(10, 150, 550));
  assert(nav.down(20, 850, 550));
  nav.move(10, 150, 450);
  nav.move(20, 950, 550);
  assert(nav.state(state) == 1);
  assert(state[2] == 1 && state[3] == 1); /* Simultaneous forward and yaw. */
  nav.up(10, 150, 450);
  nav.state(state);
  assert(state[1] == 0 && state[2] == 0 && state[3] == 1);
  assert(nav.down(30, 850, 550)); /* Occupied stick consumed, never stolen. */
  nav.move(30, 750, 550);
  nav.up(30, 750, 550);
  nav.state(state);
  assert(state[3] == 1);
  nav.up(20, 950, 550);
  nav.state(state);
  assert(state[3] == 0 && state[4] == 0);
  assert(nav.down(40, 150, 550));
  nav.move(40, 250, 550);
  nav.layout(nullptr, 0); /* Disable/resize cancels, but owns release. */
  assert(nav.owns(40));
  nav.layout(layout, 1);
  nav.move(40, 250, 550);
  assert(nav.state(state) == 0);
  nav.up(40, 250, 550);
  assert(!nav.owns(40));
  assert(nav.down(50, 500, 550));
  nav.up(50, 500, 550);
  nav.state(state);
  assert(state[5] == 1);
  nav.state(state);
  assert(state[5] == 0); /* Frame Selected once. */
  assert(nav.down(60, 150, 550));
  nav.move(60, 152, 550);
  nav.state(state);
  assert(state[1] == 0); /* Deadzone. */
  nav.cancel();
  nav.up(60, 152, 550);
  assert(nav.state(state) == 0);
  assert(nav.down(70, 150, 550));
  nav.cancel(); /* Lost input queue: no UP arrives. */
  nav.begin_sequence();
  assert(nav.down(70, 150, 550)); /* Reused pointer ID may work again. */
  nav.move(70, 250, 550);
  nav.state(state);
  assert(state[1] == 1);
  std::cout << "Android mobile ownership tests passed\n";
}
