// The sky behind the phone face: the one Home last drew on this device (its weather and the day's sun), so nothing waits
// on a request. Read once when the face opens; the top words and the logo follow it (white on the night's sky).
import { useState } from "react";

import { clockDay, readHomeLast, savedSun } from "../../../lib/homeLast";
import { homeSkyNight, homeSkyPaint } from "../../../ui/sky/HomeSky";

export function usePhoneSky() {
  const [now] = useState(() => new Date());
  const [last] = useState(() => readHomeLast());
  const day = savedSun(last, now) ?? clockDay(now);
  const condition = last?.condition ?? null;
  return { now, day, condition, paint: homeSkyPaint(now, day, condition), night: homeSkyNight(now, day, condition) };
}
