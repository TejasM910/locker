"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap, Marker as MapLibreMarker } from "maplibre-gl";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const DEFAULT_CENTER: [number, number] = [77.5946, 12.9716];
const SEARCH_RADIUS_METERS = 1_000;
const ARRIVAL_RADIUS_METERS = 50;
const MINIMUM_MOVEMENT_METERS = 50;
const MINIMUM_SEARCH_INTERVAL_MS = 15_000;
const REFRESH_INTERVAL_MS = 20_000;
const OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving";
const MINIMUM_REROUTE_INTERVAL_MS = 30_000;
const OFF_ROUTE_DISTANCE_METERS = 75;
const ROUTE_SOURCE_ID = "locker-navigation-route";
const ROUTE_CASING_LAYER_ID = "locker-navigation-route-casing";
const ROUTE_LAYER_ID = "locker-navigation-route-line";

type Coordinates = { latitude: number; longitude: number };
type RouteCoordinate = [number, number];
type LockerLocation = {
  location_id: number;
  name: string;
  address: string;
  latitude: number;
  longitude: number;
  distance_m: number;
  available_by_size: { M: number; L: number };
};
type NearbyResponse = { items: LockerLocation[]; generated_at: string };
type NavigationStep = {
  instruction: string;
  distance_m: number;
  duration_s: number;
  location: Coordinates;
};
type Navigation = {
  location_id: number;
  destination: Coordinates;
  geometry: RouteCoordinate[];
  distance_m: number;
  duration_s: number;
  steps: NavigationStep[];
  current_step: number;
};
type OsrmRouteResponse = {
  code: string;
  message?: string;
  routes?: Array<{
    distance: number;
    duration: number;
    geometry?: { coordinates?: RouteCoordinate[] };
    legs?: Array<{
      steps?: Array<{
        distance: number;
        duration: number;
        name?: string;
        maneuver?: {
          type?: string;
          modifier?: string;
          location?: RouteCoordinate;
        };
      }>;
    }>;
  }>;
};

function distanceMeters(from: Coordinates, to: Coordinates): number {
  const radians = (degrees: number) => (degrees * Math.PI) / 180;
  const latitudeDelta = radians(to.latitude - from.latitude);
  const longitudeDelta = radians(to.longitude - from.longitude);
  const haversine =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(radians(from.latitude)) *
      Math.cos(radians(to.latitude)) *
      Math.sin(longitudeDelta / 2) ** 2;
  return 6_371_008.8 * 2 * Math.asin(Math.sqrt(Math.min(1, haversine)));
}

function formatDistance(meters: number): string {
  return meters < 1_000 ? `${Math.round(meters)} m` : `${(meters / 1_000).toFixed(1)} km`;
}

function formatDuration(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

function describeManeuver(type: string, modifier: string | undefined, roadName: string | undefined): string {
  const direction = modifier ? ` ${modifier}` : "";
  const road = roadName ? ` onto ${roadName}` : "";
  if (type === "arrive") return "Arrive at the locker";
  if (type === "depart") return `Head${direction}${road}`;
  const actions: Record<string, string> = {
    turn: "Turn",
    "new name": "Continue",
    continue: "Continue",
    merge: "Merge",
    "on ramp": "Take the ramp",
    "off ramp": "Take the exit",
    fork: "Keep",
    "end of road": "At the end of the road, turn",
    "use lane": "Use the lane",
    roundabout: "Enter the roundabout",
    rotary: "Enter the rotary",
    "exit roundabout": "Exit the roundabout",
    "exit rotary": "Exit the rotary",
    notification: "Continue"
  };
  return `${actions[type] ?? "Continue"}${direction}${road}`;
}

function distanceToRouteMeters(position: Coordinates, route: RouteCoordinate[]): number {
  if (route.length < 2) return Number.POSITIVE_INFINITY;
  const radians = (degrees: number) => (degrees * Math.PI) / 180;
  const scaleX = Math.cos(radians(position.latitude)) * 6_371_008.8;
  const scaleY = 6_371_008.8;
  const originX = position.longitude;
  const originY = position.latitude;
  let minimumDistance = Number.POSITIVE_INFINITY;

  for (let index = 1; index < route.length; index += 1) {
    const [startLongitude, startLatitude] = route[index - 1];
    const [endLongitude, endLatitude] = route[index];
    const startX = radians(startLongitude - originX) * scaleX;
    const startY = radians(startLatitude - originY) * scaleY;
    const endX = radians(endLongitude - originX) * scaleX;
    const endY = radians(endLatitude - originY) * scaleY;
    const segmentX = endX - startX;
    const segmentY = endY - startY;
    const segmentLengthSquared = segmentX * segmentX + segmentY * segmentY;
    const projection = segmentLengthSquared
      ? Math.max(0, Math.min(1, -(startX * segmentX + startY * segmentY) / segmentLengthSquared))
      : 0;
    const distance = Math.hypot(startX + projection * segmentX, startY + projection * segmentY);
    minimumDistance = Math.min(minimumDistance, distance);
  }
  return minimumDistance;
}

export default function NearbyLockerMap({
  onArriveAtLocation
}: {
  onArriveAtLocation: (locationId: number) => Promise<boolean>;
}) {
  const mapElement = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerConstructorRef = useRef<typeof import("maplibre-gl").Marker | null>(null);
  const markersRef = useRef<Map<number, MapLibreMarker>>(new Map());
  const userMarkerRef = useRef<MapLibreMarker | null>(null);
  const requestRef = useRef<(coordinates: Coordinates, force?: boolean) => void>(() => undefined);
  const arrivalCallbackRef = useRef(onArriveAtLocation);
  const arrivalTriggeredRef = useRef(false);
  const arrivalPendingRef = useRef(false);
  const lastSearchRef = useRef<{ coordinates: Coordinates; at: number } | null>(null);
  const pendingSearchRef = useRef<Coordinates | null>(null);
  const searchTimerRef = useRef<number | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const routeControllerRef = useRef<AbortController | null>(null);
  const currentPositionRef = useRef<Coordinates | null>(null);
  const locationActiveRef = useRef(false);
  const navigationRef = useRef<Navigation | null>(null);
  const navigatingRef = useRef(false);
  const lastRouteRequestAtRef = useRef(0);

  const [mapReady, setMapReady] = useState(false);
  const [locations, setLocations] = useState<LockerLocation[]>([]);
  const [selected, setSelected] = useState<LockerLocation | null>(null);
  const [navigation, setNavigation] = useState<Navigation | null>(null);
  const [routeError, setRouteError] = useState("");
  const [routing, setRouting] = useState(false);
  const [arriving, setArriving] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [locationActive, setLocationActive] = useState(false);
  const [status, setStatus] = useState("Preparing the nearby map…");
  const [mapError, setMapError] = useState("");

  useEffect(() => {
    arrivalCallbackRef.current = onArriveAtLocation;
  }, [onArriveAtLocation]);

  const requestArrival = useCallback((locationId: number) => {
    if (arrivalTriggeredRef.current || arrivalPendingRef.current) return;
    arrivalPendingRef.current = true;
    setArriving(true);
    void arrivalCallbackRef.current(locationId)
      .then((loaded) => {
        if (loaded) arrivalTriggeredRef.current = true;
      })
      .catch((reason: unknown) => {
        setRouteError(reason instanceof Error ? reason.message : "Could not load lockers at this location.");
      })
      .finally(() => {
        arrivalPendingRef.current = false;
        setArriving(false);
      });
  }, []);

  const setNavigationState = useCallback((next: Navigation | null) => {
    navigationRef.current = next;
    navigatingRef.current = next !== null;
    setNavigation(next);
  }, []);

  const searchNearby = useCallback((coordinates: Coordinates, force = false) => {
    pendingSearchRef.current = coordinates;
    if (force && searchTimerRef.current !== null) {
      window.clearTimeout(searchTimerRef.current);
      searchTimerRef.current = null;
    }
    if (searchTimerRef.current !== null) return;

    const attemptSearch = () => {
      searchTimerRef.current = null;
      const target = pendingSearchRef.current;
      if (!target) return;
      const previous = lastSearchRef.current;
      if (previous && !force) {
        if (distanceMeters(previous.coordinates, target) < MINIMUM_MOVEMENT_METERS) return;
        const remaining = MINIMUM_SEARCH_INTERVAL_MS - (Date.now() - previous.at);
        if (remaining > 0) {
          searchTimerRef.current = window.setTimeout(attemptSearch, remaining);
          return;
        }
      }

      requestControllerRef.current?.abort();
      const controller = new AbortController();
      requestControllerRef.current = controller;
      lastSearchRef.current = { coordinates: target, at: Date.now() };
      setStatus("Searching locker locations…");
      setMapError("");

      void fetch(`${API_BASE}/v1/locations/nearby`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...target, radius_m: SEARCH_RADIUS_METERS }),
        signal: controller.signal
      })
        .then(async (response) => {
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw new Error(
              typeof body?.detail === "string" ? body.detail : `Location search failed (${response.status}).`
            );
          }
          return (await response.json()) as NearbyResponse;
        })
        .then((result) => {
          if (controller.signal.aborted) return;
          setLocations(result.items);
          setStatus(
            result.items.length
              ? `${result.items.length} locker location${result.items.length === 1 ? "" : "s"} within ${SEARCH_RADIUS_METERS / 1_000} km. Availability is a snapshot.`
            : `No locker locations found within ${SEARCH_RADIUS_METERS / 1_000} km. Move the map to search another area.`
          );
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          setMapError(reason instanceof Error ? reason.message : "Could not load nearby lockers.");
          setStatus("");
        });
    };

    searchTimerRef.current = window.setTimeout(attemptSearch, force ? 0 : 500);
  }, []);

  useEffect(() => {
    requestRef.current = searchNearby;
  }, [searchNearby]);

  useEffect(() => {
    let disposed = false;
    let map: MapLibreMap | null = null;
    let moveTimer: number | null = null;

    async function initializeMap() {
      try {
        const maplibre = await import("maplibre-gl");
        if (disposed || !mapElement.current) return;
        maplibre.setWorkerUrl(
          new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString()
        );
        markerConstructorRef.current = maplibre.Marker;
        const initializedMap = new maplibre.Map({
          container: mapElement.current,
          style: MAP_STYLE,
          center: DEFAULT_CENTER,
          zoom: 13
        });
        map = initializedMap;
        mapRef.current = initializedMap;
        initializedMap.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
        setMapReady(true);
        setTracking(true);
        setStatus("Requesting your location to find lockers within 1 km…");
        initializedMap.on("moveend", () => {
          if (disposed || navigatingRef.current) return;
          if (moveTimer !== null) window.clearTimeout(moveTimer);
          moveTimer = window.setTimeout(() => {
            if (disposed) return;
            const center = initializedMap.getCenter();
            requestRef.current({ latitude: center.lat, longitude: center.lng });
          }, 500);
        });
        initializedMap.on("error", (event) => {
          if (event.error) setMapError("The map tiles could not be loaded. Nearby search may still work.");
        });
      } catch {
        if (!disposed) setMapError("The map could not be initialized. Refresh the page to try again.");
      }
    }

    void initializeMap();
    return () => {
      disposed = true;
      if (moveTimer !== null) window.clearTimeout(moveTimer);
      if (searchTimerRef.current !== null) window.clearTimeout(searchTimerRef.current);
      requestControllerRef.current?.abort();
      routeControllerRef.current?.abort();
      userMarkerRef.current?.remove();
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current.clear();
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  const requestDrivingRoute = useCallback(
    async (
      destination: Pick<LockerLocation, "location_id" | "latitude" | "longitude">,
      origin: Coordinates,
      isReroute = false
    ) => {
      if (isReroute && Date.now() - lastRouteRequestAtRef.current < MINIMUM_REROUTE_INTERVAL_MS) return;
      routeControllerRef.current?.abort();
      const controller = new AbortController();
      routeControllerRef.current = controller;
      lastRouteRequestAtRef.current = Date.now();
      setRouting(true);
      setRouteError("");
      if (!isReroute) setNavigationState(null);

      try {
        const coordinates = `${origin.longitude},${origin.latitude};${destination.longitude},${destination.latitude}`;
        const response = await fetch(
          `${OSRM_ROUTE_URL}/${coordinates}?overview=full&geometries=geojson&steps=true`,
          { signal: controller.signal }
        );
        if (!response.ok) throw new Error(`Routing service returned HTTP ${response.status}.`);
        const result = (await response.json()) as OsrmRouteResponse;
        const route = result.routes?.[0];
        const geometry = route?.geometry?.coordinates;
        const steps = route?.legs?.flatMap((leg) =>
          (leg.steps ?? []).flatMap((step) => {
            const maneuver = step.maneuver;
            if (!maneuver?.location || maneuver.location.length !== 2) return [];
            return [{
              instruction: describeManeuver(maneuver.type ?? "continue", maneuver.modifier, step.name),
              distance_m: step.distance,
              duration_s: step.duration,
              location: {
                longitude: maneuver.location[0],
                latitude: maneuver.location[1]
              }
            }];
          })
        );
        if (result.code !== "Ok" || !route || !geometry || geometry.length < 2 || !steps?.length) {
          throw new Error(result.message ?? "No driving route is available to this locker.");
        }

        const nextNavigation: Navigation = {
          location_id: destination.location_id,
          destination: { latitude: destination.latitude, longitude: destination.longitude },
          geometry,
          distance_m: route.distance,
          duration_s: route.duration,
          steps,
          current_step: 0
        };
        if (controller.signal.aborted) return;
        setNavigationState(nextNavigation);
        if (
          distanceMeters(origin, nextNavigation.destination) <= ARRIVAL_RADIUS_METERS &&
          !arrivalTriggeredRef.current
        ) {
          requestArrival(destination.location_id);
        }
        if (!isReroute) {
          const map = mapRef.current;
          if (map) {
            const longitudes = geometry.map(([longitude]) => longitude);
            const latitudes = geometry.map(([, latitude]) => latitude);
            map.fitBounds(
              [
                [Math.min(...longitudes), Math.min(...latitudes)],
                [Math.max(...longitudes), Math.max(...latitudes)]
              ],
              { padding: 48, maxZoom: 16, duration: 700 }
            );
          }
        }
      } catch (reason) {
        if (controller.signal.aborted) return;
        setRouteError(
          reason instanceof Error
            ? `${reason.message} You can still select this locker and continue to booking.`
            : "Could not calculate driving directions. You can still continue to booking."
        );
        if (isReroute) navigatingRef.current = navigationRef.current !== null;
        else setNavigationState(null);
      } finally {
        if (!controller.signal.aborted) setRouting(false);
      }
    },
    [requestArrival, setNavigationState]
  );

  function selectLocation(location: LockerLocation) {
    setSelected(location);
    arrivalTriggeredRef.current = false;
    arrivalPendingRef.current = false;
    setNavigationState(null);
    routeControllerRef.current?.abort();
    setRouting(false);
    setRouteError("");
  }

  function startDrivingDirections() {
    if (!selected) return;
    const origin = currentPositionRef.current;
    if (!origin || !locationActiveRef.current) {
      setRouteError("Turn on device location to get driving directions. You can still choose this location for booking.");
      return;
    }
    void requestDrivingRoute(selected, origin);
  }

  const stopDrivingDirections = useCallback(() => {
    routeControllerRef.current?.abort();
    setRouting(false);
    setNavigationState(null);
    setRouteError("");
  }, [setNavigationState]);

  function confirmArrival() {
    if (!selected) return;
    requestArrival(selected.location_id);
  }

  const routeGeometry = navigation?.geometry ?? null;
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;

    const drawRoute = () => {
      if (!map.getSource(ROUTE_SOURCE_ID)) {
        map.addSource(ROUTE_SOURCE_ID, {
          type: "geojson",
          data: {
            type: "Feature",
            properties: {},
            geometry: routeGeometry
              ? { type: "LineString", coordinates: routeGeometry }
              : { type: "GeometryCollection", geometries: [] }
          }
        });
      } else {
        const source = map.getSource(ROUTE_SOURCE_ID);
        if (source && "setData" in source && typeof source.setData === "function") {
          void source.setData({
            type: "Feature",
            properties: {},
            geometry: routeGeometry
              ? { type: "LineString", coordinates: routeGeometry }
              : { type: "GeometryCollection", geometries: [] }
          });
        }
      }

      if (!map.getLayer(ROUTE_CASING_LAYER_ID)) {
        map.addLayer({
          id: ROUTE_CASING_LAYER_ID,
          type: "line",
          source: ROUTE_SOURCE_ID,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.9 }
        });
      }
      if (!map.getLayer(ROUTE_LAYER_ID)) {
        map.addLayer({
          id: ROUTE_LAYER_ID,
          type: "line",
          source: ROUTE_SOURCE_ID,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#176b55", "line-width": 5 }
        });
      }
    };

    if (map.isStyleLoaded()) {
      drawRoute();
      return;
    }
    map.once("load", drawRoute);
    return () => {
      map.off("load", drawRoute);
    };
  }, [mapReady, routeGeometry]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current.clear();

    for (const location of locations) {
      const hasAvailability = Object.values(location.available_by_size).some((count) => count > 0);
      const element = document.createElement("button");
      element.type = "button";
      element.className = `locker-map-marker${hasAvailability ? "" : " unavailable"}`;
      element.textContent = String(
        Object.values(location.available_by_size).reduce((total, count) => total + count, 0)
      );
      element.setAttribute(
        "aria-label",
        `${location.name}: ${hasAvailability ? "lockers available" : "no lockers available"}`
      );
      element.addEventListener("click", () => selectLocation(location));
      const Marker = markerConstructorRef.current;
      if (!Marker) continue;
      const marker = new Marker({ element, anchor: "bottom" })
        .setLngLat([location.longitude, location.latitude])
        .addTo(map);
      markersRef.current.set(location.location_id, marker);
    }
  }, [locations, mapReady]);

  useEffect(() => {
    setSelected((current) =>
      current ? locations.find((location) => location.location_id === current.location_id) ?? null : null
    );
  }, [locations]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== "visible" || !mapRef.current) return;
      const center = mapRef.current.getCenter();
      requestRef.current({ latitude: center.lat, longitude: center.lng }, true);
    };
    const timer = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  useEffect(() => {
    if (!tracking || !mapReady) return;
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      setTracking(false);
      setLocationActive(false);
      setStatus("Device location requires a secure HTTPS connection. Move the map to search manually.");
      return;
    }
    if (!navigator.geolocation) {
      setStatus("This browser does not support device location. Move the map to search manually.");
      setTracking(false);
      setLocationActive(false);
      return;
    }

    setStatus("Waiting for browser location permission…");
    let watchId: number | null = null;
    let hasCentered = false;
    const stopWatching = () => {
      if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
      }
    };
    const startWatching = () => {
      if (document.visibilityState !== "visible" || watchId !== null) return;
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          const coordinates = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude
          };
          currentPositionRef.current = coordinates;
          const map = mapRef.current;
          if (!map) return;
          if (!userMarkerRef.current) {
            const dot = document.createElement("div");
            dot.className = "traveler-location-marker";
            dot.setAttribute("aria-label", "Your current position");
            const Marker = markerConstructorRef.current;
            if (!Marker) return;
            userMarkerRef.current = new Marker({ element: dot, anchor: "center" })
              .setLngLat([coordinates.longitude, coordinates.latitude])
              .addTo(map);
          } else {
            userMarkerRef.current.setLngLat([coordinates.longitude, coordinates.latitude]);
          }
          setLocationActive(true);
          locationActiveRef.current = true;
          setLocations((current) =>
            current.flatMap((location) => {
              const distance = distanceMeters(coordinates, {
                latitude: location.latitude,
                longitude: location.longitude
              });
              return distance <= SEARCH_RADIUS_METERS ||
                navigationRef.current?.location_id === location.location_id
                ? [{ ...location, distance_m: Math.round(distance) }]
                : [];
            })
          );
          const activeNavigation = navigationRef.current;
          if (activeNavigation) {
            if (
              distanceMeters(coordinates, activeNavigation.destination) <= ARRIVAL_RADIUS_METERS &&
              !arrivalTriggeredRef.current
            ) {
              requestArrival(activeNavigation.location_id);
            }
            let currentStep = activeNavigation.current_step;
            while (
              currentStep < activeNavigation.steps.length - 1 &&
              distanceMeters(coordinates, activeNavigation.steps[currentStep + 1].location) <= 35
            ) {
              currentStep += 1;
            }
            if (currentStep !== activeNavigation.current_step) {
              setNavigationState({ ...activeNavigation, current_step: currentStep });
            }
            if (
              distanceToRouteMeters(coordinates, activeNavigation.geometry) > OFF_ROUTE_DISTANCE_METERS &&
              Date.now() - lastRouteRequestAtRef.current >= MINIMUM_REROUTE_INTERVAL_MS
            ) {
              void requestDrivingRoute(
                {
                  location_id: activeNavigation.location_id,
                  latitude: activeNavigation.destination.latitude,
                  longitude: activeNavigation.destination.longitude
                },
                coordinates,
                true
              );
            }
          }
          if (!hasCentered) {
            hasCentered = true;
            map.easeTo({ center: [coordinates.longitude, coordinates.latitude], zoom: 14, duration: 500 });
          } else {
            map.easeTo({ center: [coordinates.longitude, coordinates.latitude], duration: 300 });
          }
          requestRef.current(coordinates);
          setStatus("Location updates are active while this map is visible.");
        },
        (reason) => {
          stopWatching();
          setTracking(false);
          setLocationActive(false);
          locationActiveRef.current = false;
          currentPositionRef.current = null;
          stopDrivingDirections();
          setStatus(
            reason.code === reason.PERMISSION_DENIED
              ? "Location permission was denied. Move the map to search without sharing your location."
                : "Could not read your location. Move the map to search manually or try location again."
          );
        },
        { enableHighAccuracy: false, maximumAge: 10_000, timeout: 15_000 }
      );
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        stopWatching();
        setLocationActive(false);
        locationActiveRef.current = false;
      } else {
        startWatching();
      }
    };

    startWatching();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopWatching();
      setLocationActive(false);
      locationActiveRef.current = false;
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [tracking, mapReady, requestArrival, requestDrivingRoute, setNavigationState, stopDrivingDirections]);

  function useMyLocation() {
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      setStatus("Device location requires a secure HTTPS connection. You can still move the map manually.");
      return;
    }
    setTracking(true);
    setStatus("Requesting location permission…");
  }

  function refreshThisArea() {
    const center = mapRef.current?.getCenter();
    if (center) requestRef.current({ latitude: center.lat, longitude: center.lng }, true);
  }

  return (
    <section className="nearby-section" aria-labelledby="nearby-title">
      <div className="nearby-heading">
        <div>
          <p className="eyebrow">LIVE NEARBY SEARCH</p>
          <h2 id="nearby-title">Find a locker on the map.</h2>
          <p>Your device location is used only while this map is visible and is not saved.</p>
        </div>
        <div className="nearby-actions">
          {tracking ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setTracking(false);
                setLocationActive(false);
                locationActiveRef.current = false;
                stopDrivingDirections();
              }}
            >
              Stop location
            </button>
          ) : (
            <button type="button" className="button secondary" onClick={useMyLocation}>
              Use my location
            </button>
          )}
          <button type="button" className="text-button" onClick={refreshThisArea} disabled={!mapReady}>
            Search this area
          </button>
        </div>
      </div>
      <p className="nearby-status" role="status" aria-live="polite">
        {locationActive && <span className="tracking-indicator" aria-hidden="true" />}
        {locationActive && <span className="tracking-label">Live location on · </span>}
        {status}
      </p>
      {mapError && <p className="nearby-error" role="alert">{mapError}</p>}
      <div className="nearby-layout">
        <div ref={mapElement} className="nearby-map" aria-label="Map showing nearby locker locations" />
        <aside className="nearby-details" aria-live="polite">
          {selected ? (
            <>
              <button
                type="button"
                className="text-button nearby-back"
                onClick={() => {
                  stopDrivingDirections();
                  setSelected(null);
                }}
              >
                All nearby locations
              </button>
              <h3>{selected.name}</h3>
              <p>{selected.address || "Address not provided"}</p>
              <p className="nearby-distance">{formatDistance(selected.distance_m)} from your search location</p>
              <div className="availability-list">
                <span>Medium lockers <strong>{selected.available_by_size.M ?? 0} free</strong></span>
                <span>Large lockers <strong>{selected.available_by_size.L ?? 0} free</strong></span>
              </div>
              {navigation?.location_id === selected.location_id ? (
                <div className="navigation-panel" aria-live="polite">
                  <p className="navigation-summary">
                    Driving · {formatDistance(navigation.distance_m)} · {formatDuration(navigation.duration_s)}
                  </p>
                  <p className="navigation-next">
                    <strong>Next</strong>
                    <span>{navigation.steps[navigation.current_step]?.instruction ?? "Continue to the locker"}</span>
                  </p>
                  <ol className="navigation-steps">
                    {navigation.steps.map((step, index) => (
                      <li
                        key={`${index}-${step.instruction}`}
                        className={index === navigation.current_step ? "current" : ""}
                      >
                        <span>{step.instruction}</span>
                        <small>{formatDistance(step.distance_m)}</small>
                      </li>
                    ))}
                  </ol>
                  <button type="button" className="button secondary" onClick={stopDrivingDirections}>
                    Stop directions
                  </button>
                </div>
              ) : (
                <div className="route-disclosure">
                  <p>
                    Driving directions use the public OSRM demo service. If you request a route, your precise
                    current position and this locker&apos;s coordinates are sent to that service. Driving routes
                    only; not walking guidance.
                  </p>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={startDrivingDirections}
                    disabled={routing || !locationActive}
                  >
                    {routing ? "Finding route…" : "Start driving directions"}
                  </button>
                  {!locationActive && <small>Enable current location to start directions.</small>}
                </div>
              )}
              {routeError && <p className="nearby-error" role="alert">{routeError}</p>}
              {!Object.values(selected.available_by_size).some((count) => count > 0) && (
                <p className="nearby-unavailable">No lockers are currently available here.</p>
              )}
              <button type="button" className="button" onClick={confirmArrival} disabled={arriving}>
                {arriving ? "Loading locker options…" : "I’m here — choose a locker"}
              </button>
              <small>Arrival is normally detected within 50 m while directions and foreground location are active. Manual confirmation is a fallback and does not verify physical presence.</small>
            </>
          ) : (
            <>
              <h3>Nearby locations</h3>
              {locations.length ? (
                <ul className="nearby-list">
                  {locations.slice(0, 8).map((location) => {
                    const count = Object.values(location.available_by_size).reduce(
                      (total, available) => total + available,
                      0
                    );
                    return (
                      <li key={location.location_id}>
                        <button type="button" onClick={() => selectLocation(location)}>
                          <span className="nearby-list-name">{location.name}</span>
                          <span>{formatDistance(location.distance_m)} · {count} free</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p>Allow location access to find lockers within 1 km, or move the map to search an area.</p>
              )}
              <small>Map tiles by OpenFreeMap · © OpenStreetMap contributors</small>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
