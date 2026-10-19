function t(...e){const o=e.map(r=>r.error),a=e.map(r=>r.isFetching),n=e.map(r=>r.data!==void 0);return{error:o.find(r=>r!=null),loading:a.some(r=>r),hasData:n.every(r=>r)}}export{t as r};
