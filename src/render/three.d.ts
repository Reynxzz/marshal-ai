// three@0.186.1 ships no TypeScript declarations and the project forbids new dependencies.
// Ambient shim covering the members this render layer uses; swap for real types if three ships them.
declare module 'three' {
  export type Scene = any
  export const Scene: any
  export type Object3D = any
  export const Object3D: any
  export type Mesh = any
  export const Mesh: any
  export type Group = any
  export const Group: any
  export type Material = any
  export const Material: any
  export type MeshStandardMaterial = any
  export const MeshStandardMaterial: any
  export type BufferGeometry = any
  export const BufferGeometry: any
  export type Vector3 = any
  export const Vector3: any
  export type PerspectiveCamera = any
  export const PerspectiveCamera: any
  export type OrthographicCamera = any
  export const OrthographicCamera: any
  export type WebGLRenderer = any
  export const WebGLRenderer: any

  export const BoxGeometry: any
  export const PlaneGeometry: any
  export const CylinderGeometry: any
  export const CapsuleGeometry: any
  export const SphereGeometry: any
  export type DirectionalLight = any
  export const DirectionalLight: any
  export const AmbientLight: any
  export const Color: any
  export const MathUtils: any
  export const SRGBColorSpace: any
  export const Fog: any
  export const RingGeometry: any
  export const ConeGeometry: any
  export const MeshBasicMaterial: any
  export const HemisphereLight: any
  export const DoubleSide: any
}
