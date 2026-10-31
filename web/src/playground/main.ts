import "../../../assets/tokens.css";
import { mount } from "svelte";
import Playground from "./Playground.svelte";

const target = document.getElementById("app");
if (!target) throw new Error("page kit playground is missing its mount");
mount(Playground, { target });
